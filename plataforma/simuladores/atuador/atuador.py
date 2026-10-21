"""
atuador.py — Atuadores da brigada (camada edge): recebem comandos e executam
ações de resposta.

Fluxo:
  nó de borda (regra local) ─┐
                             ├─► .../atuador-{zona}-01/comando ─► executa ação
  central (via bridge)      ─┘                                 └─► .../evento  {"evento": "executado"}

Tópicos (formato canônico):
  assina   brigada-cerrado/{zona}/incendio/atuador-{zona}-01/comando
  publica  brigada-cerrado/{zona}/incendio/atuador-{zona}-01/evento   (resposta ao comando, RF-DIS-05)
           brigada-cerrado/{zona}/incendio/atuador-{zona}-01/estado   (a cada ESTADO_INTERVALO_S)

Um processo simula os atuadores das zonas em ZONAS, conectado ao broker da
região (o do nó de borda, ou o da central para zonas sem borda).
"""

import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # permite rodar como script

from nucleo.mensagem import nova_mensagem  # noqa: E402
from nucleo.topicos import montar_topico, parsear_topico  # noqa: E402
from nucleo.validacao import validar_mensagem  # noqa: E402
from simuladores.zonas import DOMINIO, INQUILINO, Zona, zonas_do_ambiente  # noqa: E402

load_dotenv()

# ──────────────────────────────────────────────
# Configurações
# ──────────────────────────────────────────────

BROKER    = os.getenv("MQTT_BROKER", "localhost")
PORT      = int(os.getenv("MQTT_PORT", "1883"))

# Mínimo de segundos entre ações repetidas para a mesma zona (evitar spam)
ACAO_COOLDOWN_SEG = int(os.getenv("ACAO_COOLDOWN_SEG", "30"))
ESTADO_INTERVALO_S = float(os.getenv("ESTADO_INTERVALO_S", "30"))

ZONAS_ATENDIDAS: dict[str, Zona] = {z.atuador: z for z in zonas_do_ambiente()}

# client_id estável: com sessão persistente, o broker guarda os comandos QoS 1
# que chegarem enquanto o atuador estiver desconectado
_assinatura = hashlib.sha1(",".join(sorted(ZONAS_ATENDIDAS)).encode()).hexdigest()[:8]
CLIENT_ID = os.getenv("MQTT_CLIENT_ID", f"atuadores-{_assinatura}")

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
ultimo_comando: dict[str, dict] = {}

# ──────────────────────────────────────────────
# Ações de resposta
# ──────────────────────────────────────────────

def acionar_brigada(zona: str, dados: dict) -> None:
    """
    Ação de máxima prioridade: mobilização de brigada de combate a incêndio.
    Em produção este bloco acionaria uma API de despacho, sirene física,
    notificação SMS/push, ou atuador GPIO em dispositivo embarcado.
    """
    leituras = dados.get("leituras", {})
    print("\n" + "=" * 60)
    print("  ALERTA CRITICO - BRIGADA ACIONADA")
    print(f"  Zona        : {zona.upper()}")
    print(f"  Temperatura : {leituras.get('temperatura', '?')} C")
    print(f"  Umidade     : {leituras.get('umidade', '?')} %")
    print(f"  Fumaca      : {leituras.get('fumaca', '?')} %")
    print(f"  Regra       : {dados.get('regra', '?')}  (origem: {dados.get('origem', '?')})")
    print("=" * 60 + "\n")


def reforcar_monitoramento(zona: str, dados: dict) -> None:
    """
    Ação de prioridade intermediária: aumento da frequência de leitura
    e envio de notificação de alerta para equipe de monitoramento.
    """
    leituras = dados.get("leituras", {})
    print("\n" + "-" * 60)
    print("  ALERTA ALTO - REFORCO DE MONITORAMENTO")
    print(f"  Zona        : {zona.upper()}")
    print(f"  Fumaca      : {leituras.get('fumaca', '?')} %  |  Umidade: {leituras.get('umidade', '?')} %")
    print(f"  Origem      : {dados.get('origem', '?')}")
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
# Respostas (evento e estado)
# ──────────────────────────────────────────────

def publicar(client: mqtt.Client, zona: Zona, tipo: str, dados: dict) -> None:
    mensagem = nova_mensagem(zona.atuador, tipo, dados=dados)
    topico = montar_topico(INQUILINO, zona.slug, DOMINIO, zona.atuador, tipo)
    client.publish(topico, json.dumps(mensagem, ensure_ascii=False), qos=1)


def responder(client: mqtt.Client, zona: Zona, comando: dict, resultado: str) -> None:
    """Resposta associada ao comando pelo id dele (RF-DIS-05)."""
    publicar(client, zona, "evento", {
        "evento": resultado,
        "comando_id": comando["id_mensagem"],
        "acao": comando["dados"]["acao"],
    })


def laco_estado(client: mqtt.Client, parar: threading.Event) -> None:
    while not parar.wait(ESTADO_INTERVALO_S):
        for zona in ZONAS_ATENDIDAS.values():
            ultimo = ultimo_comando.get(zona.slug, {})
            publicar(client, zona, "estado", {
                "online": True,
                "ultima_acao": ultimo.get("acao"),
                "ultimo_comando_id": ultimo.get("id"),
            })


# ──────────────────────────────────────────────
# Callbacks MQTT
# ──────────────────────────────────────────────

def on_connect(client: mqtt.Client, userdata, flags, rc: int) -> None:
    if rc == 0:
        log.info("Conectado ao broker %s:%d", BROKER, PORT)
        for zona in ZONAS_ATENDIDAS.values():
            client.subscribe(montar_topico(INQUILINO, zona.slug, DOMINIO, zona.atuador, "comando"), qos=1)
        log.info("Aguardando comandos para: %s", ", ".join(ZONAS_ATENDIDAS))
    else:
        log.error("Falha na conexão; rc=%d", rc)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    topico = parsear_topico(msg.topic)
    zona = ZONAS_ATENDIDAS.get(topico.dispositivo) if topico else None
    if zona is None:
        return

    try:
        comando = json.loads(msg.payload)
    except (json.JSONDecodeError, TypeError):
        log.warning("Payload inválido recebido em %s", msg.topic)
        return
    erros = validar_mensagem(comando, topico)
    if erros:
        log.warning("Comando fora do contrato em %s: %s", msg.topic, "; ".join(map(str, erros)))
        return

    dados = comando["dados"]
    acao = dados["acao"]

    # Cooldown por zona: evitar spam de ações idênticas
    agora = time.time()
    chave = f"{zona.slug}:{acao}"
    if agora - ultima_acao.get(chave, 0) < ACAO_COOLDOWN_SEG:
        log.info("Cooldown ativo para %s — comando %s ignorado.", chave, comando["id_mensagem"][:8])
        responder(client, zona, comando, "ignorado_cooldown")
        return
    ultima_acao[chave] = agora

    log.info("Comando recebido: zona=%-20s severidade=%-8s acao=%s",
             zona.slug, str(dados.get("severidade", "?")).upper(), acao)

    handler = ACOES.get(acao)
    if handler is None:
        log.warning("Ação desconhecida '%s' para zona %s", acao, zona.slug)
        responder(client, zona, comando, "acao_desconhecida")
        return
    handler(zona.slug, dados)
    ultimo_comando[zona.slug] = {"acao": acao, "id": comando["id_mensagem"]}
    responder(client, zona, comando, "executado")


def on_disconnect(client: mqtt.Client, userdata, rc: int) -> None:
    if rc != 0:
        log.warning("Desconexão inesperada (rc=%d); reconectando…", rc)


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────

def main() -> None:
    log.info("═══════════════════════════════════════════════")
    log.info("  Atuadores SENTINELA — Resposta a Incêndio")
    log.info("  Broker : %s:%d", BROKER, PORT)
    log.info("═══════════════════════════════════════════════")

    client = mqtt.Client(client_id=CLIENT_ID, clean_session=False)  # sessão persistente: comandos QoS 1 não se perdem
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    client.connect(BROKER, PORT, keepalive=60)
    parar = threading.Event()
    threading.Thread(target=laco_estado, args=(client, parar), daemon=True).start()

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Encerrando atuador…")
    finally:
        parar.set()
        client.disconnect()


if __name__ == "__main__":
    main()
