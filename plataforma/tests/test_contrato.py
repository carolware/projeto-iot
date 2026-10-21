"""Testes de contrato: exemplos publicados × JSON Schema (RF-INT-01, RF-ING-06)."""

import copy
import json
from pathlib import Path

import pytest

from nucleo import parsear_topico, validar_mensagem

EXEMPLOS = sorted((Path(__file__).resolve().parents[1] / "contratos" / "exemplos").glob("*.json"))


def carregar(nome: str) -> dict:
    return json.loads((EXEMPLOS[0].parent / nome).read_text(encoding="utf-8"))


@pytest.mark.parametrize("arquivo", EXEMPLOS, ids=lambda p: p.name)
def test_exemplos_publicados_sao_validos(arquivo: Path):
    assert validar_mensagem(json.loads(arquivo.read_text(encoding="utf-8"))) == []


def test_telemetria_sem_medidas_e_recusada():
    msg = carregar("telemetria-estacao.json")
    del msg["medidas"]
    erros = validar_mensagem(msg)
    assert any("medidas" in e.motivo for e in erros)


def test_comando_sem_acao_e_recusado():
    msg = carregar("comando-terminal.json")
    del msg["dados"]["acao"]
    assert validar_mensagem(msg)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("versao", "2.0"),
        ("id_mensagem", "nao-e-uuid"),
        ("dispositivo", "Est Mateiros"),
        ("tipo", "alarme"),
        ("timestamp", "19/10/2026 05:10"),
        ("timestamp", "2026-10-19T05:10:00"),  # sem fuso
        ("timestamp", "2026-13-45T05:10:00Z"),  # formato ok, data impossível
        ("campo_extra", 1),
    ],
)
def test_campos_invalidos_sao_recusados(campo: str, valor: object):
    msg = carregar("telemetria-estacao.json")
    msg[campo] = valor
    assert validar_mensagem(msg)


def test_sem_tipo_aponta_so_o_tipo():
    """Sem `tipo`, as regras condicionais (medidas/dados) não podem disparar erros falsos."""
    msg = carregar("telemetria-estacao.json")
    del msg["tipo"]
    assert [e.motivo for e in validar_mensagem(msg)] == ["'tipo' is a required property"]


def test_medida_sem_unidade_e_recusada():
    msg = carregar("telemetria-estacao.json")
    del msg["medidas"][0]["unidade"]
    erros = validar_mensagem(msg)
    assert erros and erros[0].campo == "medidas/0"


def test_geo_fora_da_faixa_e_recusado():
    msg = carregar("telemetria-estacao.json")
    msg["local"]["geo"]["coordinates"] = [-46.4, -100.0]
    assert validar_mensagem(msg)


def test_payload_que_nao_e_objeto_e_recusado():
    assert validar_mensagem(["nao", "e", "objeto"])


def test_topico_precisa_bater_com_a_mensagem():
    msg = carregar("telemetria-estacao.json")
    certo = parsear_topico("brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria")
    errado = parsear_topico("brigada-cerrado/mateiros/incendio/est-outra-02/evento")
    assert validar_mensagem(msg, certo) == []
    campos = {e.campo for e in validar_mensagem(msg, errado)}
    assert campos == {"dispositivo", "tipo"}


def test_validacao_nao_altera_a_mensagem():
    msg = carregar("telemetria-estacao.json")
    original = copy.deepcopy(msg)
    validar_mensagem(msg)
    assert msg == original
