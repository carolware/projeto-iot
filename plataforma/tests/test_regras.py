import json
from pathlib import Path

import pytest

from nucleo.regras import Regra, mais_severa, validar_regra

REGRAS_PADRAO = [
    Regra.de_dict(r)
    for r in json.loads((Path(__file__).resolve().parents[1] / "borda" / "regras-padrao.json").read_text())
]


@pytest.mark.parametrize(
    ("fumaca", "umidade", "temperatura", "esperado"),
    [
        # mesmos limiares do avaliar_risco() da 1ª entrega
        (93, 10, 42, "critico"),
        (85, 14.9, 20, "critico"),
        (85, 15, 20, "alto"),
        (60, 24, 20, "alto"),
        (60, 25, 20, "medio"),
        (40, 80, 20, "medio"),
        (10, 34, 20, "medio"),
        (10, 50, 33.1, "medio"),
        (10, 50, 33, None),
    ],
)
def test_regras_padrao_reproduzem_o_motor_antigo(fumaca, umidade, temperatura, esperado):
    regra = mais_severa(REGRAS_PADRAO, {"fumaca": fumaca, "umidade": umidade, "temperatura": temperatura})
    assert (regra.severidade if regra else None) == esperado


def test_grandeza_ausente_nao_satisfaz_o_termo():
    regra = Regra.de_dict(
        {
            "nome": "seca",
            "severidade": "alto",
            "condicao": {"todas": [{"grandeza": "umidade", "op": "<", "valor": 20}]},
        }
    )
    assert not regra.atende({"temperatura": 40})
    assert regra.atende({"umidade": 10})


@pytest.mark.parametrize(
    ("bruto", "trecho"),
    [
        (
            {"nome": "x", "severidade": "grave", "condicao": {"todas": [{"grandeza": "a", "op": ">", "valor": 1}]}},
            "severidade",
        ),
        ({"nome": "x", "severidade": "alto", "condicao": {"cada": []}}, "exatamente uma chave"),
        ({"nome": "x", "severidade": "alto", "condicao": {"todas": []}}, "ao menos um termo"),
        ({"nome": "x", "severidade": "alto", "condicao": {"todas": [{"grandeza": "a", "op": "=>", "valor": 1}]}}, "op"),
        (
            {"nome": "x", "severidade": "alto", "condicao": {"todas": [{"grandeza": "a", "op": ">", "valor": "1"}]}},
            "valor",
        ),
        ({"nome": "x", "severidade": "alto", "condicao": {"todas": [{"op": ">", "valor": 1}]}}, "grandeza"),
        ({"severidade": "alto", "condicao": {"todas": [{"grandeza": "a", "op": ">", "valor": 1}]}}, "nome"),
    ],
)
def test_regra_invalida_e_recusada(bruto, trecho):
    assert any(trecho in e for e in validar_regra(bruto))
    with pytest.raises(ValueError):
        Regra.de_dict(bruto)


def test_nenhuma_regra_atendida():
    assert mais_severa(REGRAS_PADRAO, {"fumaca": 1, "umidade": 90, "temperatura": 20}) is None
