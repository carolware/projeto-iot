"""Recebimento MQTT do nó de borda: validação local, marcação `via`, regra local e recusa."""

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from borda import app
from borda.decisao_local import DecisorLocal
from borda.fila import PRIORIDADE_ALARME, FilaLocal
from nucleo import Medida, nova_mensagem
from tests.test_borda import REGRAS


class ClienteFake:
    def __init__(self):
        self.publicadas: list[tuple[str, dict]] = []

    def publish(self, topico, carga, qos=0):
        self.publicadas.append((topico, json.loads(carga)))


@pytest.fixture
def no(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BORDA_ID", "borda-tocantins")
    monkeypatch.setattr(app, "SITES", {"mateiros"})
    fila = FilaLocal(tmp_path / "fila.db", Fernet.generate_key())
    no = app.NoBorda(fila, DecisorLocal(REGRAS, "borda-tocantins"))
    yield no, fila
    fila.fechar()


def receber(no, topico: str, carga) -> ClienteFake:
    cliente = ClienteFake()
    payload = carga if isinstance(carga, bytes) else json.dumps(carga).encode()
    no._ao_receber(cliente, None, SimpleNamespace(topic=topico, payload=payload))
    return cliente


TOPICO = "brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria"


def leitura(fumaca=10.0, umidade=50.0):
    return nova_mensagem(
        "est-mateiros-01",
        "telemetria",
        medidas=[Medida("temperatura", 30, "Cel"), Medida("umidade", umidade, "%"), Medida("fumaca", fumaca, "1")],
    )


def test_leitura_valida_entra_na_fila_marcada_com_a_borda(no):
    no_borda, fila = no
    receber(no_borda, TOPICO, leitura())
    [item] = fila.proximas(10)
    assert item.mensagem["via"] == "borda-tocantins"


def test_risco_critico_comanda_o_atuador_e_enfileira_alarme_prioritario(no):
    no_borda, fila = no
    cliente = receber(no_borda, TOPICO, leitura(fumaca=93, umidade=8))
    [(topico, comando)] = cliente.publicadas
    assert topico == "brigada-cerrado/mateiros/incendio/atuador-mateiros-01/comando"
    assert comando["dados"]["acao"] == "acionar_brigada"
    primeiro = fila.proximas(10)[0]
    assert primeiro.prioridade == PRIORIDADE_ALARME and primeiro.mensagem["dados"]["evento"] == "alarme_local"


def test_reenvio_do_dispositivo_nao_reavalia_regra(no):
    no_borda, fila = no
    msg = leitura(fumaca=93, umidade=8)
    receber(no_borda, TOPICO, msg)
    assert receber(no_borda, TOPICO, msg).publicadas == []
    assert fila.contagem()["pendente"] == 2  # leitura + alarme, uma vez cada


@pytest.mark.parametrize(
    ("topico", "carga"),
    [
        ("brigada-cerrado/formosa/incendio/est-formosa-01/telemetria", "valida"),  # site de outra borda
        (TOPICO, b"{nao e json"),
        (TOPICO, {"versao": "1.0"}),
        ("brigada-cerrado/mateiros/incendio/est-outra-01/telemetria", "valida"),  # tópico ≠ dispositivo
    ],
)
def test_mensagem_invalida_nao_entra_na_fila(no, topico, carga):
    no_borda, fila = no
    receber(no_borda, topico, leitura() if carga == "valida" else carga)
    assert fila.contagem()["pendente"] == 0
    with fila._lock:
        assert fila._conexao.execute("SELECT count(*) FROM invalidas").fetchone()[0] == 1
