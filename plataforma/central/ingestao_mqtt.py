"""
ingestao_mqtt.py — serviço de ingestão MQTT da central (central-ingestao).

Assina os tópicos dos dispositivos no broker central, passa cada micro-lote
pelo pipeline do nucleo (validar → deduplicar → enriquecer → gravar) e publica
as aceitas no barramento `plataforma/...`, de onde a API alimenta o motor de
regras e o tempo real. Separado da API para a ingestão ter um núcleo de CPU
só dela (RNF-01); com CENTRAL_MQTT_GRUPO, réplicas dividem as mensagens por
assinatura compartilhada do MQTT 5/Mosquitto (RNF-09).

Execução:  python -m central.ingestao_mqtt      métricas em :9100/metrics
"""

import os
import signal
import socket
import threading

from prometheus_client import start_http_server

from central.config import configuracao
from central.db import criar_engine
from central.metricas import PipelineMedido
from central.mqtt import AdaptadorMqtt, BarramentoMqtt
from central.repositorios import CatalogoPostgres, FilaInvalidasPostgres, RepositorioPostgres
from nucleo.logs import configurar_logs

INSTANCIA = "ingestao"


def main() -> None:
    configurar_logs("central-ingestao")
    cfg = configuracao()
    engine = criar_engine(cfg.database_url)
    grupo = os.getenv("CENTRAL_MQTT_GRUPO") or None
    # client_id estável: a sessão persistente guarda as mensagens enquanto a réplica reinicia.
    # Sem CENTRAL_MQTT_CLIENT_ID vale o hostname, que muda quando o contêiner é recriado — a
    # sessão antiga continuaria no grupo compartilhado, recebendo a parte dela das mensagens.
    client_id = cfg.mqtt_client_id or f"central-ingestao-{socket.gethostname()}"
    mqtt = AdaptadorMqtt(cfg.mqtt_broker, cfg.mqtt_port, client_id, grupo=grupo)
    pipeline = PipelineMedido(
        CatalogoPostgres(engine),
        RepositorioPostgres(engine),
        FilaInvalidasPostgres(engine),
        BarramentoMqtt(mqtt.cliente, INSTANCIA),
    )
    start_http_server(int(os.getenv("METRICAS_PORTA", "9100")))
    mqtt.iniciar(pipeline)

    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    parar.wait()
    mqtt.parar()
    engine.dispose()


if __name__ == "__main__":
    main()
