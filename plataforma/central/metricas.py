"""
metricas.py — métricas da instância central em formato Prometheus (RF-OBS-01),
expostas em /metrics: vazão, latência e erros por módulo.

    sentinela_mensagens_total{canal,status}          ingestão (mqtt/http/lote × aceita/duplicada/recusada)
    sentinela_ingestao_latencia_segundos{canal}      medição na origem → aceita na central (RNF-02)
    sentinela_pipeline_segundos{canal}               tempo do pipeline por chamada (validar…gravar)
    sentinela_mqtt_lote_mensagens                    tamanho dos micro-lotes da ingestão MQTT
    sentinela_regra_latencia_segundos                medição → alarme aberto pela central (RNF-02)
    sentinela_alarmes_total{severidade,origem}       alarmes abertos (central/borda)
    sentinela_acoes_total{tipo,status}               execuções de ações (sucesso/falha)
    sentinela_motor_fila                             mensagens aguardando o motor de regras
    sentinela_tempo_real_conexoes                    painéis conectados ao WebSocket
    sentinela_http_duracao_segundos{metodo,rota,status}
"""

import time
from collections.abc import Callable
from datetime import datetime

from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram

from nucleo.ingestao import Entrada, PipelineIngestao, Resultado
from nucleo.mensagem import agora_utc, parsear_timestamp

TRACER = trace.get_tracer("central.ingestao")
_LATENCIAS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60, 300, 3600, 86400)

MENSAGENS = Counter("sentinela_mensagens_total", "Mensagens processadas pela ingestão", ["canal", "status"])
LATENCIA_INGESTAO = Histogram(
    "sentinela_ingestao_latencia_segundos",
    "Da medição na origem até a mensagem ser aceita na central (inclui o tempo guardado na borda)",
    ["canal"],
    buckets=_LATENCIAS,
)
PIPELINE = Histogram(
    "sentinela_pipeline_segundos", "Duração de uma chamada do pipeline (1 mensagem ou 1 lote)", ["canal"]
)
LOTE_MQTT = Histogram(
    "sentinela_mqtt_lote_mensagens",
    "Mensagens por micro-lote da ingestão MQTT",
    buckets=(1, 5, 10, 50, 100, 250, 500, 1000),
)
LATENCIA_REGRA = Histogram(
    "sentinela_regra_latencia_segundos", "Da medição até o alarme aberto pela central", buckets=_LATENCIAS
)
ALARMES = Counter("sentinela_alarmes_total", "Alarmes abertos", ["severidade", "origem"])
ACOES = Counter("sentinela_acoes_total", "Execuções de ações de regra", ["tipo", "status"])
MOTOR_FILA = Gauge("sentinela_motor_fila", "Mensagens aguardando o motor de regras")
TEMPO_REAL = Gauge("sentinela_tempo_real_conexoes", "Conexões WebSocket de tempo real abertas")
HTTP = Histogram("sentinela_http_duracao_segundos", "Duração das requisições HTTP", ["metodo", "rota", "status"])


def canal_de(origem: str) -> str:
    if "lote" in origem:
        return "lote"
    return origem.split(":", 1)[0] if ":" in origem else origem


class PipelineMedido(PipelineIngestao):
    """O pipeline do nucleo, com contadores e latências (o domínio não conhece Prometheus)."""

    def processar_entradas(self, entradas: list[Entrada], autorizado=None) -> list[Resultado]:
        # um span por chamada (mensagem HTTP, lote da borda ou micro-lote MQTT), não por mensagem (RF-OBS-03)
        with TRACER.start_as_current_span("ingestao.pipeline") as span:
            inicio = time.perf_counter()
            resultados = super().processar_entradas(entradas, autorizado)
            _anotar(span, entradas, resultados)
        if entradas:
            PIPELINE.labels(canal_de(entradas[0].origem)).observe(time.perf_counter() - inicio)
        agora = agora_utc()
        for r in resultados:
            canal = canal_de(entradas[r.indice].origem)
            MENSAGENS.labels(canal, r.status).inc()
            if r.status == "aceita":
                medido = _medido_em(entradas[r.indice].bruto)
                if medido:
                    LATENCIA_INGESTAO.labels(canal).observe(max(0.0, (agora - medido).total_seconds()))
        return resultados


def _anotar(span, entradas: list[Entrada], resultados: list[Resultado]) -> None:
    if not span.is_recording():
        return
    span.set_attribute("sentinela.canal", canal_de(entradas[0].origem) if entradas else "")
    span.set_attribute("sentinela.mensagens", len(entradas))
    for status in ("aceita", "duplicada", "recusada"):
        span.set_attribute(f"sentinela.{status}s", sum(r.status == status for r in resultados))
    # id_mensagem liga o trace aos logs; num lote grande, só os primeiros para não inflar o span
    ids = [r.id_mensagem for r in resultados if r.id_mensagem][:20]
    if ids:
        span.set_attribute("sentinela.id_mensagem", ids)


def _medido_em(bruto: object) -> datetime | None:
    try:
        return parsear_timestamp(bruto["timestamp"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None


def ligar_medidores(fila_motor: Callable[[], int], conexoes: Callable[[], int]) -> None:
    MOTOR_FILA.set_function(fila_motor)
    TEMPO_REAL.set_function(conexoes)
