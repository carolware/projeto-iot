"""Área geográfica nas regras (RF-PRO-02) e séries por área (RF-DAD-01)."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from nucleo import Medida, nova_mensagem
from tests.central.conftest import ANAPOLIS, BRIGADA
from tests.central.test_regras_alarmes import abertos, enviar

TALHAO = {"slug": "talhao-11", "nome": "Talhão 11", "lat": -10.80, "lon": -46.42, "raio_km": 2}


@pytest.fixture(scope="module")
def lavoura(cliente):
    """Sonda no talhão 11 (cerca de 28 km ao sul da sede) e outra só no site, sem área."""
    cliente.post("/api/v1/catalogo/dominios", json={"slug": "lavoura", "nome": "Lavoura"}, headers=BRIGADA)
    cliente.post(
        "/api/v1/catalogo/perfis",
        headers=BRIGADA,
        json={
            "dominio": "lavoura",
            "slug": "sonda-solo",
            "nome": "Sonda de umidade do solo",
            "intervalo_esperado_s": 600,
            "grandezas": [{"grandeza": "umidade_solo", "unidade": "%", "valor_min": 0, "valor_max": 100}],
        },
    )
    cliente.post("/api/v1/sites/mateiros/areas", json=TALHAO, headers=BRIGADA)
    for slug, area in (("sonda-talhao-11", "talhao-11"), ("sonda-sede", None)):
        cliente.post(
            "/api/v1/dispositivos",
            headers=BRIGADA,
            json={
                "slug": slug,
                "nome": slug,
                "tipo": "estacao",
                "dominio": "lavoura",
                "perfil": "sonda-solo",
                "site": "mateiros",
                **({"area": area} if area else {}),
            },
        )
    return "sonda-talhao-11", "sonda-sede"


def solo(dispositivo, valor, quando=None, geo=None):
    return nova_mensagem(
        dispositivo,
        "telemetria",
        medidas=[Medida("umidade_solo", valor, "%")],
        timestamp=quando or datetime.now(UTC),
        local={"geo": {"type": "Point", "coordinates": list(geo)}} if geo else None,
    )


def criar_regra(cliente, nome, area):
    regra = {
        "dominio": "lavoura",
        "nome": nome,
        "severidade": "alto",
        "condicao": {"todas": [{"grandeza": "umidade_solo", "op": "<", "valor": 20}], "area": area},
    }
    resposta = cliente.post("/api/v1/regras", json=regra, headers=BRIGADA)
    assert resposta.status_code == 201, resposta.text


def test_regra_por_area_cadastrada_so_vale_dentro_dela(cliente, lavoura):
    dentro, fora = lavoura
    criar_regra(cliente, "solo-seco-talhao-11", "talhao-11")
    enviar(cliente, solo(dentro, 12), solo(fora, 12))
    assert [a["regra"] for a in abertos(cliente, dentro)] == ["solo-seco-talhao-11"]
    assert abertos(cliente, fora) == []
    enviar(cliente, solo(dentro, 45))  # volta ao normal: resolve


def test_regra_por_circulo_usa_a_posicao_da_mensagem_ou_a_do_cadastro(cliente, lavoura):
    dentro, fora = lavoura
    criar_regra(cliente, "solo-seco-raio", {"lat": TALHAO["lat"], "lon": TALHAO["lon"], "raio_km": 5})
    enviar(cliente, solo(fora, 10))  # sem geo: vale a sede (fora do raio)
    assert abertos(cliente, fora) == []
    enviar(cliente, solo(fora, 10, geo=(-46.43, -10.81)))  # GeoJSON lon, lat: dentro do raio
    assert [a["regra"] for a in abertos(cliente, fora)] == ["solo-seco-raio"]
    enviar(cliente, solo(fora, 45, geo=(-46.43, -10.81)))


def test_regra_com_area_invalida_ou_na_borda_e_recusada(cliente):
    base = {"dominio": "lavoura", "nome": "x", "severidade": "alto"}
    termos = [{"grandeza": "umidade_solo", "op": "<", "valor": 20}]
    for condicao, escopo in (
        ({"todas": termos, "area": {"lat": 0, "lon": 0}}, "central"),
        ({"ausencia": {"segundos": 60}, "area": "talhao-11"}, "central"),
        ({"todas": termos, "area": "talhao-11"}, "borda"),
    ):
        corpo = base | {"condicao": condicao, "escopo": escopo}
        assert cliente.post("/api/v1/regras", json=corpo, headers=BRIGADA).status_code == 422, condicao


def test_series_por_area_e_intervalo(cliente, lavoura):
    """RF-DAD-01: 24 h de uma área, filtradas por grandeza, em até 1 s."""
    dentro, fora = lavoura
    agora = datetime.now(UTC)
    enviar(
        cliente, *[solo(dentro, 40 + i % 5, agora - timedelta(hours=24) + timedelta(minutes=30 * i)) for i in range(48)]
    )
    enviar(cliente, solo(fora, 50, agora - timedelta(hours=1)))

    inicio = time.perf_counter()
    resposta = cliente.get(
        "/api/v1/telemetria",
        params={
            "area": "talhao-11",
            "grandeza": "umidade_solo",
            "inicio": (agora - timedelta(hours=24)).isoformat(),
            "fim": (agora + timedelta(minutes=1)).isoformat(),
            "tamanho": 500,
        },
        headers=BRIGADA,
    )
    assert time.perf_counter() - inicio < 1.0
    corpo = resposta.json()
    assert resposta.status_code == 200 and corpo["total"] >= 48
    assert {p["dispositivo"] for p in corpo["itens"]} == {dentro} and {p["area"] for p in corpo["itens"]} == {
        "talhao-11"
    }

    por_hora = cliente.get(
        "/api/v1/telemetria", params={"site": "mateiros", "dispositivo": fora, "agregacao": "1h"}, headers=BRIGADA
    )
    assert por_hora.status_code == 200

    # isolamento: outro inquilino não enxerga a área da brigada
    assert cliente.get("/api/v1/telemetria", params={"area": "talhao-11"}, headers=ANAPOLIS).json()["total"] == 0
