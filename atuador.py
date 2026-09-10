"""
atuador.py — Atuador IoT: recebe comandos do gateway e executa ações de resposta

Fluxo:
  gateway → MQTT (topico atuador) → atuador → executa ação (log, sirene, brigada)

Tópicos subscritos:  sentinela-iot-2026-joao-carol/monitoramento-br/+/atuador/alerta

O atuador é o último elo da cadeia IoT: ao receber um pacote de alerta ele
decide qual ação executar com base no nível de risco e em se o satélite NASA
FIRMS confirmou o foco de incêndio.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Configurações
# ──────────────────────────────────────────────

BROKER    = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT      = int(os.getenv("MQTT_PORT", "1883"))
CLIENT_ID = "atuador-incendio-florestal"
TOPIC_BASE   = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/monitoramento-br")
SUB_PATTERN  = f"{TOPIC_BASE}/+/atuador/alerta"   # escuta todos os alertas

# Mínimo de segundos entre ações repetidas para a mesma zona (evitar spam)
ACAO_COOLDOWN_SEG = int(os.getenv("ACAO_COOLDOWN_SEG", "30"))

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [atuador] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("atuador")

# ──────────────────────────────────────────────
# Controle de cooldown por zona
# ──────────────────────────────────────────────

ultima_acao: dict[str, float] = {}

# ──────────────────────────────────────────────
# Ações de resposta
# ──────────────────────────────────────────────

def acionar_brigada(zona: str, dados: dict) -> None:
    """
    Ação de máxima prioridade: mobilização de brigada de combate a incêndio.
    Em produção este bloco acionaria uma API de despacho, sirene física,
    notificação SMS/push, ou atuador GPIO em dispositivo embarcado.
    """
    frp = dados.get("firms_frp_max", 0)
    dist = dados.get("firms_dist_km")
    dist_str = f"{dist:.1f} km" if dist is not None else "desconhecida"

    print("\n" + "=" * 60)
    print("  ALERTA CRITICO - BRIGADA ACIONADA")
    print(f"  Zona        : {zona.upper()}")
    print(f"  Temperatura : {dados.get('temperatura', '?')} C")
    print(f"  Umidade     : {dados.get('umidade', '?')} %")
    print(f"  Fumaca      : {dados.get('fumaca', '?')} %")
    print(f"  FIRMS       : {'CONFIRMADO' if dados.get('firms_confirmado') else 'NAO confirmado'}")
    if dados.get("firms_confirmado"):
        print(f"  Focos NASA  : {dados.get('firms_focos', 0)} | FRP={frp} W/m2 | dist={dist_str}")
    print(f"  Timestamp   : {dados.get('timestamp', '?')}")
    print("=" * 60 + "\n")

    # Aqui entraria: requests.post("https://api-defesa-civil/despacho", json={...})


def reforcar_monitoramento(zona: str, dados: dict) -> None:
    """
    Ação de prioridade intermediária: aumento da frequência de leitura
    e envio de notificação de alerta para equipe de monitoramento.
    """
    print("\n" + "-" * 60)
    print("  ALERTA ALTO - REFORCO DE MONITORAMENTO")
    print(f"  Zona        : {zona.upper()}")
    print(f"  Fumaca      : {dados.get('fumaca', '?')} %  |  Umidade: {dados.get('umidade', '?')} %")
    print(f"  FIRMS       : {'CONFIRMADO' if dados.get('firms_confirmado') else 'aguardando satelite'}")
    print("-" * 60 + "\n")


def silenciar_alerta(zona: str, dados: dict) -> None:
    """
    Ação de cancelamento: risco voltou ao normal, desacionar alarmes ativos.
    """
    log.info("[silenciar] %s — alerta encerrado", zona)


ACOES = {
    "acionar_brigada":         acionar_brigada,
    "reforcar_monitoramento":  reforcar_monitoramento,
    "silenciar_alerta":        silenciar_alerta,
}


# ──────────────────────────────────────────────
# Callbacks MQTT
# ──────────────────────────────────────────────

def on_connect(client: mqtt.Client, userdata, flags, rc: int) -> None:
    if rc == 0:
        log.info("Conectado ao broker %s:%d", BROKER, PORT)
        client.subscribe(SUB_PATTERN, qos=1)
        log.info("Subscrito em '%s'", SUB_PATTERN)
    else:
        log.error("Falha na conexão; rc=%d", rc)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    """
    Decodifica o tópico  sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/atuador/alerta
    e executa a ação correspondente ao campo 'acao' do payload JSON.
    """
    parts = msg.topic.split("/")
    if len(parts) < 5 or parts[-2] != "atuador":
        return

    zona = parts[-3]

    try:
        dados = json.loads(msg.payload)
    except (json.JSONDecodeError, TypeError):
        log.warning("Payload inválido recebido em %s", msg.topic)
        return

    acao = dados.get("acao", "reforcar_monitoramento")
    risco = dados.get("risco", "desconhecido")

    # Cooldown por zona: evitar spam de ações idênticas
    agora = time.time()
    chave = f"{zona}:{acao}"
    if agora - ultima_acao.get(chave, 0) < ACAO_COOLDOWN_SEG:
        log.debug("Cooldown ativo para %s — ação ignorada.", chave)
        return

    ultima_acao[chave] = agora

    log.info(
        "Alerta recebido: zona=%-22s risco=%-8s acao=%s",
        zona, risco.upper(), acao,
    )

    handler = ACOES.get(acao)
    if handler:
        handler(zona, dados)
    else:
        log.warning("Ação desconhecida '%s' para zona %s", acao, zona)


def on_disconnect(client: mqtt.Client, userdata, rc: int) -> None:
    if rc != 0:
        log.warning("Desconexão inesperada (rc=%d); reconectando…", rc)


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────

def main() -> None:
    log.info("═══════════════════════════════════════════════")
    log.info("  Atuador SENTINELA — Resposta a Incêndio")
    log.info("  Broker : %s:%d", BROKER, PORT)
    log.info("  Aguardando alertas em '%s'", SUB_PATTERN)
    log.info("═══════════════════════════════════════════════")

    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    client.connect(BROKER, PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Encerrando atuador…")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
