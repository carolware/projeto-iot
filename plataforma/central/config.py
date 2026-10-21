"""
config.py — configuração da central lida do ambiente (Twelve-Factor).
"""

import os
from dataclasses import dataclass
from functools import cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Configuracao:
    database_url: str
    mqtt_broker: str
    mqtt_port: int
    mqtt_ingestao: bool
    mqtt_client_id: str | None = None


def _url_padrao() -> str:
    usuario = os.getenv("POSTGRES_USER", "sentinela")
    senha = os.getenv("POSTGRES_PASSWORD", "sentinela")
    banco = os.getenv("POSTGRES_DB", "sentinela")
    porta = os.getenv("POSTGRES_PORT", "5433")
    return f"postgresql+psycopg://{usuario}:{senha}@localhost:{porta}/{banco}"


@cache
def configuracao() -> Configuracao:
    load_dotenv()
    return Configuracao(
        database_url=os.getenv("DATABASE_URL") or _url_padrao(),
        mqtt_broker=os.getenv("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        mqtt_ingestao=os.getenv("CENTRAL_MQTT", "1") == "1",
        mqtt_client_id=os.getenv("CENTRAL_MQTT_CLIENT_ID") or None,
    )
