import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nucleo.regras import Regra, concorrem, mais_severa, sustentada, validar_regra, vencedoras

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


# ── Escopo, ausência, ações, concorrência e janela ─────────────────────────


def regra(nome="r", severidade="alto", grandeza="temperatura", op=">", valor=30, **extra):
    return Regra.de_dict(
        {
            "nome": nome,
            "severidade": severidade,
            "condicao": {"todas": [{"grandeza": grandeza, "op": op, "valor": valor}]},
        }
        | extra
    )


@pytest.mark.parametrize(
    ("extra", "trecho"),
    [
        ({"escopo": "borda", "janela_s": 60}, "escopo borda"),
        ({"escopo": "nuvem"}, "escopo"),
        ({"janela_s": -1}, "janela_s"),
        ({"acoes": [{"tipo": "sirene"}]}, "tipo deve ser"),
        ({"acoes": [{"tipo": "comando"}]}, "falta acao"),
        ({"acoes": [{"tipo": "notificar", "canal": "pombo", "destino": "x"}]}, "canal"),
        ({"acoes": [{"tipo": "webhook", "url": "ftp://x"}]}, "http"),
        ({"acoes": "alarme"}, "lista"),
    ],
)
def test_escopo_janela_e_acoes_sao_validados(extra, trecho):
    bruto = {
        "nome": "x",
        "severidade": "alto",
        "condicao": {"todas": [{"grandeza": "a", "op": ">", "valor": 1}]},
    } | extra
    assert any(trecho in e for e in validar_regra(bruto))


def test_regra_de_ausencia():
    r = Regra.de_dict(
        {
            "nome": "calada",
            "severidade": "medio",
            "condicao": {"ausencia": {"segundos": 300}},
            "acoes": [{"tipo": "alarme"}],
        }
    )
    assert r.de_ausencia and r.gera_alarme and r.grandezas() == set()
    assert not r.atende({"temperatura": 99})
    assert validar_regra({"nome": "x", "severidade": "medio", "condicao": {"ausencia": {"segundos": 0}}})
    borda = {"nome": "x", "severidade": "medio", "escopo": "borda", "condicao": {"ausencia": {"segundos": 60}}}
    assert any("escopo borda" in e for e in validar_regra(borda))


def test_ida_e_volta_preserva_a_regra():
    r = regra(janela_s=600, escopo="central", acoes=[{"tipo": "alarme"}], id="abc")
    assert Regra.de_dict(r.para_dict()) == r


def test_so_regras_concorrentes_se_anulam():
    critica = regra("critica", "critico", "fumaca", ">=", 85)
    alta = regra("alta", "alto", "fumaca", ">=", 60)
    umidade = regra("umidade-baixa", "medio", "umidade", "<", 20)
    assert concorrem(critica, alta) and not concorrem(critica, umidade)
    assert {r.nome for r in vencedoras([critica, alta, umidade])} == {"critica", "umidade-baixa"}


T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
QUENTE = regra(janela_s=1800)
TOL = timedelta(minutes=20)


def serie(*pontos):
    return [(T0 + timedelta(minutes=m), {"temperatura": v}) for m, v in pontos]


@pytest.mark.parametrize(
    ("pontos", "fim_min", "esperado"),
    [
        (((-45, 25), (-30, 31), (-15, 32), (0, 33)), 0, True),  # valia desde o início exato da janela
        (((-45, 25), (-30, 31), (-15, 32), (-1, 33)), -1, False),  # a leitura de 25 °C está dentro da tolerância
        (((-30, 31), (-15, 29), (0, 33)), 0, False),  # caiu no meio
        (((-5, 31), (0, 33)), 0, False),  # dispositivo novo: dado não cobre o começo
        (((-25, 31), (-10, 32), (0, 33)), 0, True),  # novo, mas primeira leitura dentro da tolerância
        (((-120, 20), (-29, 31), (0, 33)), 0, True),  # âncora antiga demais é ignorada; usa a tolerância
        ((), 0, False),
    ],
)
def test_janela_de_tempo(pontos, fim_min, esperado):
    assert sustentada(QUENTE, serie(*pontos), T0 + timedelta(minutes=fim_min), TOL) is esperado
