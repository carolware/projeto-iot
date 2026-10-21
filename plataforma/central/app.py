"""
app.py — instância central (cloud) da plataforma: API REST (OpenAPI 3 em
/docs e /openapi.json), ingestão HTTP/lote e ingestão MQTT, todas sobre o
mesmo pipeline do nucleo.

Execução:  uvicorn central.app:app --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from central.api import catalogo, dispositivos, ingestao, operacao
from central.config import Configuracao, configuracao
from central.db import criar_engine
from central.mqtt import AdaptadorMqtt, BarramentoMqtt
from central.repositorios import CatalogoPostgres, FilaInvalidasPostgres, RepositorioPostgres
from nucleo.ingestao import PipelineIngestao
from nucleo.validacao import SCHEMA_V1, diretorio_contratos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [central] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)


def criar_app(cfg: Configuracao | None = None) -> FastAPI:
    cfg = cfg or configuracao()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        engine = criar_engine(cfg.database_url)
        mqtt = AdaptadorMqtt(cfg.mqtt_broker, cfg.mqtt_port) if cfg.mqtt_ingestao else None
        app.state.engine = engine
        app.state.pipeline = PipelineIngestao(
            CatalogoPostgres(engine),
            RepositorioPostgres(engine),
            FilaInvalidasPostgres(engine),
            BarramentoMqtt(mqtt.cliente) if mqtt else None,
        )
        if mqtt:
            mqtt.iniciar(app.state.pipeline)
        yield
        if mqtt:
            mqtt.parar()
        engine.dispose()

    app = FastAPI(
        title="Sentinela — Plataforma IoT Integrada (central)",
        version="1.0.0",
        description="API da instância central. Contrato MQTT em /contratos/asyncapi.yaml; "
        "mensagem canônica em /contratos/mensagem-canonica.v1.schema.json.",
        lifespan=ciclo_de_vida,
    )
    for modulo in (ingestao, catalogo, dispositivos, operacao):
        app.include_router(modulo.router)

    # Contratos publicados por URL (RF-INT-01)
    raiz = diretorio_contratos()
    publicados = {SCHEMA_V1, "asyncapi.yaml"} | {f"exemplos/{p.name}" for p in (raiz / "exemplos").glob("*.json")}

    @app.get("/contratos/{caminho:path}", tags=["Contratos"])
    def contrato(caminho: str):
        if caminho not in publicados:
            raise HTTPException(404, f"contratos disponíveis: {sorted(publicados)}")
        return FileResponse(raiz / caminho)

    @app.get("/saude", tags=["Operação"])
    def saude():
        return {"status": "ok"}

    return app


app = criar_app()
