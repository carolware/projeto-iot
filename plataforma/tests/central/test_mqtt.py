"""
Ingestão MQTT da central contra um broker real (mosquitto-central do compose).
Pulado se não houver broker em MQTT_TESTE_BROKER (padrão localhost:1883).
"""

import json
import os
import socket
import threading
import uuid

import paho.mqtt.client as mqtt
import pytest

from central.mqtt import AdaptadorMqtt, BarramentoMqtt
from nucleo import Medida, nova_mensagem
from nucleo.ingestao import PipelineIngestao
from tests.test_ingestao import ESTACAO, CatalogoFake, FilaFake, RepositorioFake

BROKER = os.getenv("MQTT_TESTE_BROKER", "localhost")
PORTA = 1883


def _broker_disponivel() -> bool:
    try:
        socket.create_connection((BROKER, PORTA), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _broker_disponivel(), reason="broker MQTT indisponível")


class Ouvinte:
    """Assina um filtro e acorda o teste quando chegar a primeira mensagem."""

    def __init__(self, filtro: str):
        self.recebidas: list[tuple[str, dict]] = []
        self.evento = threading.Event()
        self.cliente = mqtt.Client(client_id=f"teste-{uuid.uuid4().hex[:8]}")
        self.cliente.on_message = self._ao_receber
        self.cliente.connect(BROKER, PORTA)
        self.cliente.subscribe(filtro, qos=1)
        self.cliente.loop_start()

    def _ao_receber(self, cliente, userdata, msg):
        self.recebidas.append((msg.topic, json.loads(msg.payload)))
        self.evento.set()

    def parar(self):
        self.cliente.loop_stop()
        self.cliente.disconnect()


@pytest.fixture
def central():
    repo, fila = RepositorioFake(), FilaFake()
    adaptador = AdaptadorMqtt(BROKER, PORTA)
    pipeline = PipelineIngestao(CatalogoFake(ESTACAO), repo, fila, BarramentoMqtt(adaptador.cliente))
    conectado = threading.Event()
    ao_conectar = adaptador.cliente.on_connect

    def _sinalizar(*args):
        ao_conectar(*args)
        conectado.set()

    adaptador.cliente.on_connect = _sinalizar
    adaptador.iniciar(pipeline)
    assert conectado.wait(5)
    yield repo, fila
    adaptador.parar()


def _publicar(topico: str, carga) -> None:
    cliente = mqtt.Client()
    cliente.connect(BROKER, PORTA)
    cliente.loop_start()
    cliente.publish(topico, carga if isinstance(carga, str) else json.dumps(carga), qos=1).wait_for_publish(5)
    cliente.loop_stop()
    cliente.disconnect()


def test_telemetria_no_topico_canonico_e_republicada_enriquecida(central):
    repo, _ = central
    ouvinte = Ouvinte("plataforma/telemetria/brigada-cerrado/#")
    msg = nova_mensagem("est-mateiros-01", "telemetria", medidas=[Medida("umidade", 18, "%")])
    _publicar("brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria", msg)
    assert ouvinte.evento.wait(5)
    ouvinte.parar()
    topico, carga = ouvinte.recebidas[0]
    assert topico == "plataforma/telemetria/brigada-cerrado/mateiros/incendio/est-mateiros-01"
    assert carga["id_mensagem"] == msg["id_mensagem"] and carga["contexto"]["dominio"] == "incendio"
    assert msg["id_mensagem"] in repo.gravadas


@pytest.mark.parametrize(
    ("topico", "carga"),
    [
        ("pref-anapolis/mateiros/incendio/est-mateiros-01/telemetria", "valida-de-outro-inquilino"),
        ("brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria", "{nao e json"),
    ],
)
def test_recusada_vai_para_a_dlq_do_barramento(central, topico, carga):
    _, fila = central
    if carga == "valida-de-outro-inquilino":
        carga = nova_mensagem("est-mateiros-01", "telemetria", medidas=[Medida("umidade", 18, "%")])
    ouvinte = Ouvinte("plataforma/dlq")
    _publicar(topico, carga)
    assert ouvinte.evento.wait(5)
    ouvinte.parar()
    assert ouvinte.recebidas[0][1]["origem"] == f"mqtt:{topico}"
    assert len(fila.recusas) == 1


def test_topico_fora_da_convencao_e_ignorado(central):
    repo, fila = central
    _publicar("brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria/extra", {"x": 1})
    _publicar(
        "brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria",
        nova_mensagem("est-mateiros-01", "telemetria", medidas=[Medida("umidade", 18, "%")]),
    )
    # a segunda mensagem chega; a primeira nunca entra no pipeline
    for _ in range(50):
        if repo.gravadas:
            break
        threading.Event().wait(0.1)
    assert len(repo.gravadas) == 1 and fila.recusas == []
