"""
config.py — configuração da central lida do ambiente (Twelve-Factor).
"""

import os
from dataclasses import dataclass, field
from functools import cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Configuracao:
    database_url: str
    mqtt_broker: str
    mqtt_port: int
    mqtt_ingestao: bool
    mqtt_client_id: str | None = None
    # identidade (Keycloak / OIDC)
    oidc_emissor: str = "http://localhost:8080/realms/sentinela"
    oidc_jwks_url: str | None = None
    oidc_audiencia: str = "sentinela-api"
    # broker central: onde gravar senhas/ACL e senhas dos usuários de serviço
    mqtt_acesso_dir: str | None = None
    mqtt_senhas_servicos: dict[str, str] = field(default_factory=dict)
    mqtt_topico_legado: str = "sentinela-iot-2026-joao-carol/monitoramento-br"


def _url_padrao() -> str:
    usuario = os.getenv("POSTGRES_USER", "sentinela")
    senha = os.getenv("POSTGRES_PASSWORD", "sentinela")
    banco = os.getenv("POSTGRES_DB", "sentinela")
    porta = os.getenv("POSTGRES_PORT", "5433")
    return f"postgresql+psycopg://{usuario}:{senha}@localhost:{porta}/{banco}"


@cache
def configuracao() -> Configuracao:
    load_dotenv()
    emissor = os.getenv("OIDC_EMISSOR", "http://localhost:8080/realms/sentinela").rstrip("/")
    return Configuracao(
        database_url=os.getenv("DATABASE_URL") or _url_padrao(),
        mqtt_broker=os.getenv("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "8883")),
        mqtt_ingestao=os.getenv("CENTRAL_MQTT", "1") == "1",
        mqtt_client_id=os.getenv("CENTRAL_MQTT_CLIENT_ID") or None,
        oidc_emissor=emissor,
        oidc_jwks_url=os.getenv("OIDC_JWKS_URL") or f"{emissor}/protocol/openid-connect/certs",
        oidc_audiencia=os.getenv("OIDC_AUDIENCIA", "sentinela-api"),
        mqtt_acesso_dir=os.getenv("MQTT_ACESSO_DIR") or None,
        mqtt_senhas_servicos={
            nome: os.getenv(f"MQTT_SENHA_{nome.upper()}", "") for nome in ("central", "ponte", "painel", "simulacao")
        },
        mqtt_topico_legado=os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/monitoramento-br"),
    )
