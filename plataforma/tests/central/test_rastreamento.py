"""
RF-OBS-03: um trace começa no nó de borda (lote de sincronização), atravessa a
API da central e chega ao pipeline e às consultas SQL — com o mesmo trace_id.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from opentelemetry import propagate, trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from borda import app as borda_app
from central.api.seguranca import Autenticador
from central.app import criar_app
from central.config import Configuracao
from nucleo.logs import FormatoJson
from nucleo.rastreamento import configurar_rastreamento
from tests.central.conftest import _CHAVE, AUDIENCIA, BORDA_GOIAS, EMISSOR
from tests.central.test_api import leitura

TRACER = trace.get_tracer("teste")


@pytest.fixture(scope="module")
def spans():
    exportador = InMemorySpanExporter()
    assert configurar_rastreamento("teste", exportador=exportador)
    return exportador


@pytest.fixture
def central_rastreada(url_banco, spans):
    app = criar_app(
        Configuracao(database_url=url_banco, mqtt_broker="", mqtt_port=0, mqtt_ingestao=False),
        publicar=lambda topico, carga: None,
        motor_em_segundo_plano=False,
        autenticador=Autenticador(EMISSOR, AUDIENCIA, chave_publica=_CHAVE.public_key()),
    )
    with TestClient(app) as cliente:
        spans.clear()
        yield cliente


def test_lote_da_borda_continua_o_trace_na_central(central_rastreada, spans):
    with TRACER.start_as_current_span("borda.sincronizar_lote") as origem:
        cabecalhos = {}
        propagate.inject(cabecalhos)  # o que o requests instrumentado faz no nó de borda
    msg = leitura("est-pirenopolis-01")
    resposta = central_rastreada.post(
        "/api/v1/ingestao/lote", json={"mensagens": [msg]}, headers=BORDA_GOIAS | cabecalhos
    )
    assert resposta.status_code == 200 and resposta.json()["aceitas"] == 1

    trace_id = origem.get_span_context().trace_id
    do_trace = [s for s in spans.get_finished_spans() if s.context.trace_id == trace_id]
    nomes = {s.name for s in do_trace}
    assert "ingestao.pipeline" in nomes
    assert any(n.startswith("POST /api/v1/ingestao/lote") for n in nomes)
    assert any(s.attributes.get("db.system") == "postgresql" for s in do_trace), "consultas SQL no mesmo trace"
    [pipeline] = [s for s in do_trace if s.name == "ingestao.pipeline"]
    assert pipeline.attributes["sentinela.aceitas"] == 1
    assert list(pipeline.attributes["sentinela.id_mensagem"]) == [msg["id_mensagem"]]


def test_borda_envia_traceparent_no_lote(spans, monkeypatch):
    spans.clear()
    recebidos = {}

    class Central(BaseHTTPRequestHandler):
        def do_POST(self):
            recebidos["traceparent"] = self.headers.get("traceparent")
            self.rfile.read(int(self.headers["Content-Length"]))
            corpo = json.dumps({"resultados": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Central)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    monkeypatch.setattr(borda_app, "CENTRAL_URL", f"http://127.0.0.1:{servidor.server_address[1]}")
    try:
        borda_app.enviar_lote([leitura()])
    finally:
        servidor.shutdown()

    [lote] = [s for s in spans.get_finished_spans() if s.name == "borda.sincronizar_lote"]
    assert recebidos["traceparent"].split("-")[1] == format(lote.context.trace_id, "032x")


def test_log_dentro_de_um_span_leva_o_trace_id(spans):
    with TRACER.start_as_current_span("qualquer") as span:
        registro = logging.LogRecord("t", logging.INFO, "", 0, "mensagem", (), None)
        linha = json.loads(FormatoJson("teste").format(registro))
    assert linha["trace_id"] == format(span.get_span_context().trace_id, "032x")
