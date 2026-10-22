"""Micro-lote da ingestão MQTT (RNF-01): o callback só enfileira; a gravação junta o que acumulou."""

import json
from types import SimpleNamespace

from central.mqtt import AdaptadorMqtt
from nucleo import nova_mensagem

TOPICO = "brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria"


class PipelineEspiao:
    def __init__(self):
        self.lotes = []

    def processar_entradas(self, entradas, autorizado=None):
        self.lotes.append(entradas)
        return []


def test_mensagens_acumuladas_viram_um_lote_so(monkeypatch):
    monkeypatch.delenv("MQTT_CA", raising=False)
    adaptador = AdaptadorMqtt("localhost", 1883)
    adaptador.pipeline = PipelineEspiao()
    for _ in range(3):
        payload = json.dumps(nova_mensagem("est-mateiros-01", "telemetria")).encode()
        adaptador._ao_receber(None, None, SimpleNamespace(topic=TOPICO, payload=payload))
    adaptador._ao_receber(None, None, SimpleNamespace(topic="fora/do/padrao", payload=b"{}"))
    adaptador._fila.put(None)  # encerra a thread depois do lote

    adaptador._gravar()

    [lote] = adaptador.pipeline.lotes
    assert len(lote) == 3
    assert {e.origem for e in lote} == {f"mqtt:{TOPICO}"} and lote[0].topico.dispositivo == "est-mateiros-01"


class Coletor:
    def __init__(self):
        self.recebidas = []

    def publicar_aceita(self, mensagem):
        self.recebidas.append(mensagem)

    def publicar_recusada(self, recusa):
        pass


def test_api_ouve_no_barramento_o_que_o_servico_de_ingestao_gravou(monkeypatch):
    """Ingestão separada: a mensagem enriquecida atravessa o barramento e volta igual; a própria é ignorada."""
    from central.mqtt import BarramentoMqtt, topico_barramento
    from nucleo.ingestao import DispositivoCadastrado, MedidaEnriquecida, MensagemEnriquecida
    from nucleo.mensagem import Medida, Mensagem, agora_utc

    monkeypatch.delenv("MQTT_CA", raising=False)
    original = MensagemEnriquecida(
        Mensagem.de_dict(nova_mensagem("est-mateiros-01", "telemetria", medidas=[Medida("temperatura", 41.5, "Cel")])),
        DispositivoCadastrado(
            "11111111-1111-1111-1111-111111111111",
            "est-mateiros-01",
            "brigada-cerrado",
            "mateiros",
            "incendio",
            "estacao-incendio",
        ),
        (MedidaEnriquecida("temperatura", 41.5, "Cel", suspeita=True),),
        agora_utc(),
    )
    publicadas = []
    cliente = SimpleNamespace(publish=lambda topico, carga, qos: publicadas.append((topico, carga)))

    api, coletor = AdaptadorMqtt("localhost", 1883, ingerir=False), Coletor()
    api.ouvir_barramento(coletor, ignorar_instancia="api")
    for instancia in ("ingestao", "api"):
        BarramentoMqtt(cliente, instancia).publicar_aceita(original)
    for topico, carga in publicadas:
        api._ao_receber_do_barramento(None, None, SimpleNamespace(topic=topico, payload=carga.encode()))

    [recebida] = coletor.recebidas
    assert publicadas[0][0] == topico_barramento(original)
    assert recebida.dispositivo.id == original.dispositivo.id and recebida.medidas == original.medidas
    assert recebida.mensagem.timestamp == original.mensagem.timestamp and recebida.id_mensagem == original.id_mensagem
