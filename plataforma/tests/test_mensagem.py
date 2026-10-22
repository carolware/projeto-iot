import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nucleo import Medida, Mensagem, nova_mensagem, validar_mensagem
from nucleo.mensagem import formatar_timestamp, parsear_timestamp


def test_nova_mensagem_gera_telemetria_valida():
    msg = nova_mensagem(
        "est-mateiros-01",
        "telemetria",
        medidas=[Medida("temperatura", 34.2, "Cel"), Medida("umidade", 18.0, "%")],
        via="borda-tocantins",
        fonte="open-meteo",
    )
    assert validar_mensagem(msg) == []
    assert uuid.UUID(msg["id_mensagem"])
    assert msg["timestamp"].endswith("Z")


def test_cada_mensagem_tem_id_proprio():
    a = nova_mensagem("terminal-dc-01", "estado", dados={"online": True})
    b = nova_mensagem("terminal-dc-01", "estado", dados={"online": True})
    assert a["id_mensagem"] != b["id_mensagem"]


def test_campos_opcionais_vazios_nao_aparecem():
    msg = nova_mensagem("terminal-dc-01", "evento", dados={"evento": "recebido"})
    assert set(msg) == {"versao", "id_mensagem", "dispositivo", "tipo", "timestamp", "dados"}


def test_ida_e_volta_preserva_a_mensagem():
    original = nova_mensagem(
        "est-mateiros-01",
        "telemetria",
        medidas=[Medida("fumaca", 62.5, "1")],
        local={"site": "mateiros", "geo": {"type": "Point", "coordinates": [-46.4168, -10.5464]}},
    )
    assert Mensagem.de_dict(original).para_dict() == original


def test_timestamp_do_passado_e_mantido():
    """Dado atrasado (ex.: 48 h, vindo da borda) carrega o horário da medição, não o de envio."""
    medido = datetime(2026, 10, 17, 5, 10, tzinfo=UTC)
    msg = nova_mensagem("est-mateiros-01", "telemetria", medidas=[Medida("umidade", 20, "%")], timestamp=medido)
    assert Mensagem.de_dict(msg).timestamp == medido


def test_timestamp_e_normalizado_para_utc():
    brasilia = timezone(timedelta(hours=-3))
    assert formatar_timestamp(datetime(2026, 10, 19, 2, 10, tzinfo=brasilia)) == "2026-10-19T05:10:00.000Z"
    assert parsear_timestamp("2026-10-19T02:10:00-03:00") == datetime(2026, 10, 19, 5, 10, tzinfo=UTC)


def test_timestamp_sem_fuso_e_rejeitado():
    with pytest.raises(ValueError):
        formatar_timestamp(datetime(2026, 10, 19, 5, 10))
    with pytest.raises(ValueError):
        parsear_timestamp("2026-10-19T05:10:00")
