"""
app.py — instância central (cloud) da plataforma: API REST (OpenAPI 3 em
/docs e /openapi.json), ingestão HTTP/lote e ingestão MQTT, todas sobre o
mesmo pipeline do nucleo. Cada mensagem aceita segue, pelo barramento, para o
MQTT (plataforma/...) e para o motor de regras.

Execução:  uvicorn central.app:app --host 0.0.0.0 --port 8000
"""

import json
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import make_asgi_app

from central.acesso_mqtt import GestorAcesso, servicos_da_central
from central.acoes import AcaoComando, AcaoNotificar, AcaoPublicar, AcaoWebhook, ExecutorAcoes, Publicador
from central.api import alarmes, catalogo, dispositivos, ingestao, operacao, painel, regras, telemetria
from central.api.seguranca import Autenticador
from central.config import Configuracao, configuracao
from central.db import criar_engine
from central.metricas import HTTP, PipelineMedido, ligar_medidores
from central.motor import MotorRegras
from central.mqtt import AdaptadorMqtt, BarramentoMqtt
from central.repositorios import CatalogoPostgres, FilaInvalidasPostgres, RepositorioPostgres
from central.tempo_real import HubTempoReal
from nucleo.credenciais import hash_senha
from nucleo.ingestao import BarramentoComposto
from nucleo.logs import configurar_logs
from nucleo.validacao import SCHEMA_V1, diretorio_contratos

configurar_logs("central")
INSTANCIA = "api"  # marca, no barramento, o que esta instância ingeriu (lote HTTP, ingestão embutida)


def criar_app(
    cfg: Configuracao | None = None,
    publicar: Publicador | None = None,
    estrategias: dict | None = None,
    motor_em_segundo_plano: bool = True,
    autenticador: Autenticador | None = None,
) -> FastAPI:
    """
    `publicar`, `estrategias`, `motor_em_segundo_plano` e `autenticador` existem
    para os testes: capturar o que seria publicado no MQTT, trocar o cliente
    HTTP do webhook, processar regras de forma síncrona e validar tokens
    assinados por uma chave local em vez do Keycloak.
    """
    cfg = cfg or configuracao()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        engine = criar_engine(cfg.database_url)
        mqtt = (
            AdaptadorMqtt(cfg.mqtt_broker, cfg.mqtt_port, cfg.mqtt_client_id, ingerir=not cfg.mqtt_ingestao_separada)
            if cfg.mqtt_ingestao
            else None
        )
        hub = HubTempoReal()
        publicar_externo = publicar or (_publicador_mqtt(mqtt) if mqtt else (lambda topico, carga: None))

        def publicador(topico: str, carga: dict) -> None:
            publicar_externo(topico, carga)
            hub.de_topico(topico, carga)  # alarmes também vão para os painéis conectados

        executor = ExecutorAcoes(
            engine,
            {
                "comando": AcaoComando(engine, publicador),
                "webhook": AcaoWebhook(),
                "publicar": AcaoPublicar(publicador),
                "notificar": AcaoNotificar(),
            }
            | (estrategias or {}),
        )
        motor = MotorRegras(engine, executor, publicador)
        servicos = servicos_da_central(cfg.mqtt_senhas_servicos)
        simulacao = cfg.mqtt_senhas_servicos.get("simulacao")
        acesso = GestorAcesso(engine, cfg.mqtt_acesso_dir, servicos, hash_senha(simulacao) if simulacao else None)
        acesso.atualizar()  # antes de conectar: o próprio usuário "central" precisa estar no broker
        app.state.engine = engine
        app.state.publicar = publicador
        app.state.motor = motor
        app.state.hub = hub
        app.state.acesso = acesso
        app.state.autenticador = autenticador or Autenticador(
            cfg.oidc_emissor, cfg.oidc_audiencia, jwks_url=cfg.oidc_jwks_url
        )
        app.state.pipeline = PipelineMedido(
            CatalogoPostgres(engine),
            RepositorioPostgres(engine),
            FilaInvalidasPostgres(engine),
            # hub antes do motor: o painel recebe a leitura antes do alarme que ela provoca
            BarramentoComposto(*([BarramentoMqtt(mqtt.cliente, INSTANCIA)] if mqtt else []), hub, motor),
        )
        if mqtt and cfg.mqtt_ingestao_separada:
            mqtt.ouvir_barramento(BarramentoComposto(hub, motor), ignorar_instancia=INSTANCIA)
        ligar_medidores(motor._fila.qsize, hub.conexoes)
        if motor_em_segundo_plano:
            motor.iniciar()
        if mqtt:
            mqtt.iniciar(app.state.pipeline)
        yield
        if mqtt:
            mqtt.parar()
        motor.parar()
        engine.dispose()

    app = FastAPI(
        title="Sentinela — Plataforma IoT Integrada (central)",
        version="1.0.0",
        description="API da instância central. Contrato MQTT em /contratos/asyncapi.yaml; "
        "mensagem canônica em /contratos/mensagem-canonica.v1.schema.json.",
        lifespan=ciclo_de_vida,
    )

    # painel web em outra origem (RF-APP-06); só as origens configuradas
    @app.middleware("http")
    async def medir_http(request, chamar):
        inicio = time.perf_counter()
        resposta = await chamar(request)
        rota = getattr(request.scope.get("route"), "path", "desconhecida")  # modelo da rota, não a URL concreta
        HTTP.labels(request.method, rota, resposta.status_code).observe(time.perf_counter() - inicio)
        return resposta

    # métricas Prometheus (RF-OBS-01); coletadas pelo serviço prometheus do compose
    app.mount("/metrics", make_asgi_app())

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origens,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Inquilino"],
    )
    for modulo in (ingestao, catalogo, dispositivos, telemetria, regras, alarmes, operacao, painel):
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


def _publicador_mqtt(mqtt: AdaptadorMqtt) -> Callable[[str, dict], None]:
    def publicar(topico: str, carga: dict) -> None:
        mqtt.cliente.publish(topico, json.dumps(carga, ensure_ascii=False), qos=1)

    return publicar


app = criar_app()
