"""
Testes de integração da central contra um PostgreSQL + TimescaleDB real.

Usa um banco descartável (`sentinela_teste`) no mesmo servidor do compose; se
não houver servidor acessível, os testes são pulados. No CI, o banco vem de um
service container (ver .github/workflows/ci.yml).
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from central.app import criar_app
from central.config import Configuracao, configuracao
from central.seeds.seed import semear

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "central" / "alembic.ini"


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


@pytest.fixture(scope="session")
def cliente(url_banco):
    app = criar_app(Configuracao(database_url=url_banco, mqtt_broker="", mqtt_port=0, mqtt_ingestao=False))
    with TestClient(app) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def banco(url_banco):
    engine = create_engine(url_banco)
    yield engine
    engine.dispose()


BRIGADA = {"X-Inquilino": "brigada-cerrado", "X-Usuario": "teste@sentinela"}
ANAPOLIS = {"X-Inquilino": "pref-anapolis", "X-Usuario": "operador@anapolis"}
