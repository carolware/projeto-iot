"""
Testes de integração da central contra um PostgreSQL + TimescaleDB real.

Usa um banco descartável (`sentinela_teste`) no mesmo servidor do compose; se
não houver servidor acessível, os testes são pulados. No CI, o banco vem de um
service container (ver .github/workflows/ci.yml).
"""

import base64
import os
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from central.acoes import AcaoWebhook
from central.api.seguranca import Autenticador
from central.app import criar_app
from central.config import Configuracao, configuracao
from central.seeds.seed import semear

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "central" / "alembic.ini"

# ── Identidade de teste: tokens no formato do Keycloak, assinados por uma chave local ──
EMISSOR = "http://keycloak.teste/realms/sentinela"
AUDIENCIA = "sentinela-api"
_CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)

SEGREDO_PROVISIONAMENTO = "segredo-de-teste"
CREDENCIAIS_BORDA = {
    "borda-tocantins": "credencial-borda-tocantins-teste",
    "borda-goias": "credencial-borda-goias-teste",
}
os.environ["SEGREDO_PROVISIONAMENTO"] = SEGREDO_PROVISIONAMENTO
for _slug, _credencial in CREDENCIAIS_BORDA.items():
    os.environ[f"CREDENCIAL_{_slug.upper().replace('-', '_')}"] = _credencial


def token(usuario: str, inquilino: str | None, *papeis: str, expira_em: int = 300, **extra) -> str:
    claims = {
        "iss": EMISSOR,
        "aud": AUDIENCIA,
        "sub": usuario,
        "preferred_username": usuario,
        "exp": int(time.time()) + expira_em,
        "realm_access": {"roles": list(papeis)},
    } | ({"inquilino": inquilino} if inquilino else {})
    return jwt.encode(claims | extra, _CHAVE, algorithm="RS256")


def bearer(*args, **kwargs) -> dict:
    return {"Authorization": f"Bearer {token(*args, **kwargs)}"}


def basic(usuario: str, senha: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{usuario}:{senha}".encode()).decode()}


BRIGADA = bearer("teste@sentinela", "brigada-cerrado", "gestor")
ANAPOLIS = bearer("operador@anapolis", "pref-anapolis", "gestor")
ADMIN = bearer("admin@sentinela", None, "admin")
BORDA_GOIAS = basic("borda-goias", CREDENCIAIS_BORDA["borda-goias"])


def _url_teste() -> str:
    return os.getenv("TEST_DATABASE_URL") or make_url(configuracao().database_url).set(
        database="sentinela_teste"
    ).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def url_banco():
    url = make_url(_url_teste())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conexao:
            conexao.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conexao.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError:
        pytest.skip("PostgreSQL/TimescaleDB indisponível (suba com: docker compose up -d postgres)")
    finally:
        admin.dispose()

    texto = url.render_as_string(hide_password=False)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = texto
    command.upgrade(cfg, "head")

    engine = create_engine(texto)
    with engine.begin() as conexao:
        semear(conexao)
    engine.dispose()
    return texto


class Publicadas(list):
    """Tudo o que a central publicaria no MQTT: (tópico, carga)."""

    def em(self, prefixo: str) -> list[dict]:
        return [carga for topico, carga in self if topico.startswith(prefixo)]


class WebhookFake:
    """Substitui requests.post: responde com os códigos programados (padrão 200)."""

    def __init__(self):
        self.respostas: list[int] = []
        self.chamadas: list[tuple[str, dict]] = []

    def __call__(self, url, json=None, timeout=None):
        self.chamadas.append((url, json))
        return SimpleNamespace(status_code=self.respostas.pop(0) if self.respostas else 200)


@pytest.fixture(scope="session")
def publicadas():
    return Publicadas()


@pytest.fixture(scope="session")
def webhook():
    return WebhookFake()


@pytest.fixture(scope="session")
def cliente(url_banco, publicadas, webhook):
    app = criar_app(
        Configuracao(database_url=url_banco, mqtt_broker="", mqtt_port=0, mqtt_ingestao=False),
        publicar=lambda topico, carga: publicadas.append((topico, carga)),
        estrategias={"webhook": AcaoWebhook(post=webhook)},
        motor_em_segundo_plano=False,  # regras processadas na hora: asserções determinísticas
        autenticador=Autenticador(EMISSOR, AUDIENCIA, chave_publica=_CHAVE.public_key()),
    )
    with TestClient(app) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def banco(url_banco):
    engine = create_engine(url_banco)
    yield engine
    engine.dispose()
