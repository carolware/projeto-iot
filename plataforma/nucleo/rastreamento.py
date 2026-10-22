"""
rastreamento.py — rastreamento distribuído com OpenTelemetry (RF-OBS-03).

Ligado só quando OTEL_EXPORTER_OTLP_ENDPOINT está definido (no compose, o
Jaeger em http://jaeger:4318); sem ele, tudo aqui é no-op e os spans criados
pelo código não custam nada. Amostragem pelas variáveis padrão do SDK:
OTEL_TRACES_SAMPLER=parentbased_traceidratio e OTEL_TRACES_SAMPLER_ARG=0.1.

O que vira trace:
- requisições da API (FastAPI) e as consultas SQL que elas fazem (SQLAlchemy);
- chamadas HTTP de saída (requests) — o lote borda→central leva o cabeçalho
  `traceparent`, então o trace começa no nó de borda e continua na central;
- o pipeline de ingestão e, na borda, o recebimento e a sincronização.

MQTT 3.1.1 não tem cabeçalhos: mensagem que chega por MQTT começa um trace novo
na ingestão; a ligação com o resto é o `id_mensagem` (atributo do span e campo
dos logs, que também levam o `trace_id`).
"""

import logging
import os

log = logging.getLogger("rastreamento")
_ligado = False


def ligado() -> bool:
    return _ligado


def configurar_rastreamento(servico: str, exportador=None) -> bool:
    """
    Instala o provedor de traces com exportação OTLP/HTTP; devolve False se desligado.
    `exportador` existe para os testes (spans em memória, sem Jaeger).
    """
    global _ligado
    if _ligado or not (exportador or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")):
        return _ligado
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provedor = TracerProvider(resource=Resource.create({"service.name": servico}))  # amostrador vem do ambiente
    provedor.add_span_processor(
        SimpleSpanProcessor(exportador) if exportador else BatchSpanProcessor(OTLPSpanExporter())
    )
    trace.set_tracer_provider(provedor)
    RequestsInstrumentor().instrument()
    _ligado = True
    log.info("OpenTelemetry ligado: %s → %s", servico, os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "exportador local"))
    return True


def instrumentar_engine(engine) -> None:
    """
    Usa o EngineTracer do pacote de instrumentação direto, engine a engine: o
    SQLAlchemyInstrumentor só aceita SQLAlchemy < 2.1 (usamos 2.1) e, por ser
    singleton, instrumentaria só a primeira engine do processo.
    """
    if _ligado:
        from opentelemetry import metrics, trace
        from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer

        uso = metrics.get_meter("sentinela.sqlalchemy").create_up_down_counter(
            "db.client.connections.usage", unit="connections"
        )
        EngineTracer(trace.get_tracer("sentinela.sqlalchemy"), engine, uso)


def instrumentar_fastapi(app) -> None:
    if _ligado:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,saude")


def trace_id_atual() -> str | None:
    """Id do trace em andamento (para os logs), ou None fora de um span amostrado."""
    if not _ligado:
        return None
    from opentelemetry import trace

    contexto = trace.get_current_span().get_span_context()
    return format(contexto.trace_id, "032x") if contexto.is_valid else None
