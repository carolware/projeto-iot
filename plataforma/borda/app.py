"""
app.py — nó de borda (camada fog). Substitui o gateway da 1ª entrega.

    estações ──MQTT local──► [validar → fila cifrada → regras locais] ──lotes HTTP──► central
                                          │
                                          └── comando local ao atuador (funciona sem link)

Os comandos da central para os dispositivos do campo chegam pela bridge do
Mosquitto local (ver infra/mosquitto/borda-*.conf), que os guarda enquanto o
link está fora. As regras locais vêm da central (escopo "borda", RF-EDG-04) e
ficam salvas no disco do nó: reiniciar sem link usa a última versão recebida.

Execução:  python -m borda.app
"""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv
from prometheus_client import Counter, Gauge, start_http_server

from borda.decisao_local import DecisorLocal
from borda.fila import PRIORIDADE_ALARME, PRIORIDADE_ESTADO, PRIORIDADE_TELEMETRIA, FilaLocal
from borda.sincronizador import LinkIndisponivel, Sincronizador
from nucleo.regras import Regra
from nucleo.topicos import padrao_assinatura, parsear_topico
from nucleo.validacao import validar_mensagem

load_dotenv()

# ──────────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────────

BORDA_ID = os.getenv("BORDA_ID", "borda-tocantins")
INQUILINO = os.getenv("BORDA_INQUILINO", "brigada-cerrado")
SITES = {s.strip() for s in os.getenv("BORDA_SITES", "mateiros,lagoa-da-confusao,sandolandia").split(",") if s.strip()}
BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
CENTRAL_URL = os.getenv("CENTRAL_URL", "http://localhost:8000").rstrip("/")
CHAVE = os.getenv("BORDA_CHAVE", "")
ARQUIVO_FILA = os.getenv("BORDA_FILA", f"/dados/{BORDA_ID}.db")
REGRAS = os.getenv("BORDA_REGRAS", str(Path(__file__).with_name("regras-padrao.json")))
VERSAO = "0.2.0"

SYNC_INTERVALO_S = float(os.getenv("SYNC_INTERVALO_S", "1"))
SYNC_BACKOFF_MAX_S = float(os.getenv("SYNC_BACKOFF_MAX_S", "30"))
LOTE_MAX = int(os.getenv("LOTE_MAX", "500"))
ESTADO_INTERVALO_S = float(os.getenv("ESTADO_INTERVALO_S", "10"))
REGRAS_INTERVALO_S = float(os.getenv("REGRAS_INTERVALO_S", "30"))
REGRAS_SINCRONIZADAS = Path(os.getenv("BORDA_REGRAS_SINCRONIZADAS", str(Path(ARQUIVO_FILA).with_name("regras.json"))))
RETENCAO_ENVIADAS_S = float(os.getenv("RETENCAO_ENVIADAS_S", "3600"))
ALERTA_COOLDOWN_SEG = float(os.getenv("ALERTA_COOLDOWN_SEG", "30"))
METRICAS_PORTA = int(os.getenv("METRICAS_PORTA", "9100"))

PRIORIDADES = {"telemetria": PRIORIDADE_TELEMETRIA, "estado": PRIORIDADE_ESTADO, "evento": PRIORIDADE_ESTADO}

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{BORDA_ID}] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("borda")

# ──────────────────────────────────────────────
# Métricas (RF-OBS-01 / RF-EDG-06)
# ──────────────────────────────────────────────

M_RECEBIDAS = Counter("borda_mensagens_recebidas_total", "Mensagens recebidas dos dispositivos", ["tipo"])
M_INVALIDAS = Counter("borda_mensagens_invalidas_total", "Mensagens recusadas na validação local")
M_SINCRONIZADAS = Counter("borda_mensagens_sincronizadas_total", "Mensagens confirmadas pela central")
M_ALARMES = Counter("borda_alarmes_locais_total", "Alarmes disparados por regras locais", ["severidade"])
M_PENDENTES = Gauge("borda_fila_pendente", "Mensagens aguardando envio à central")
M_LINK = Gauge("borda_link_central_online", "1 se a última tentativa de sincronizar funcionou")
M_ULTIMO_SYNC = Gauge("borda_ultimo_sync_timestamp", "Epoch da última sincronização bem-sucedida")


# ──────────────────────────────────────────────
# Ingestão local
# ──────────────────────────────────────────────


class NoBorda:
    def __init__(self, fila: FilaLocal, decisor: DecisorLocal):
        self.fila = fila
        self.decisor = decisor
        self.cliente = mqtt.Client(client_id=f"{BORDA_ID}-{uuid.uuid4().hex[:6]}", clean_session=True)
        self.cliente.on_connect = self._ao_conectar
        self.cliente.on_message = self._ao_receber

    def iniciar(self) -> None:
        self.cliente.connect_async(BROKER, PORT, keepalive=60)
        self.cliente.loop_start()

    def _ao_conectar(self, cliente, userdata, flags, rc: int) -> None:
        if rc != 0:
            log.error("Falha ao conectar no broker local %s:%d (rc=%d)", BROKER, PORT, rc)
            return
        for tipo in ("telemetria", "evento", "estado"):
            cliente.subscribe(padrao_assinatura(tipo, INQUILINO), qos=1)
        log.info("Ingestão local ativa em %s:%d — sites: %s", BROKER, PORT, ", ".join(sorted(SITES)))

    def _ao_receber(self, cliente, userdata, msg: mqtt.MQTTMessage) -> None:
        topico = parsear_topico(msg.topic)
        if topico is None or topico.site not in SITES:
            self._recusar(msg, "tópico fora da convenção ou de site não atendido por este nó")
            return
        try:
            bruto = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._recusar(msg, "payload não é JSON")
            return
        erros = validar_mensagem(bruto, topico)
        if erros:
            self._recusar(msg, "; ".join(str(e) for e in erros))
            return

        bruto["via"] = BORDA_ID
        M_RECEBIDAS.labels(bruto["tipo"]).inc()
        if not self.fila.enfileirar(bruto, PRIORIDADES[bruto["tipo"]]):
            return  # reenvio do dispositivo: já estava na fila

        decisao = self.decisor.avaliar(bruto, topico)
        if decisao:
            for topico_cmd, comando in decisao.comandos:
                cliente.publish(topico_cmd, json.dumps(comando, ensure_ascii=False), qos=1)
            self.fila.enfileirar(decisao.alarme, PRIORIDADE_ALARME)
            M_ALARMES.labels(decisao.regra.severidade).inc()
            log.warning(
                "ALARME LOCAL %s em %s (%s) → %s",
                decisao.regra.severidade.upper(),
                topico.site,
                decisao.regra.nome,
                ", ".join(c["dados"]["acao"] for _, c in decisao.comandos) or "sem comando",
            )

    def _recusar(self, msg: mqtt.MQTTMessage, motivo: str) -> None:
        M_INVALIDAS.inc()
        self.fila.registrar_invalida(f"mqtt:{msg.topic}", msg.payload, motivo)
        log.warning("Recusada %s: %s", msg.topic, motivo)


# ──────────────────────────────────────────────
# Link com a central
# ──────────────────────────────────────────────


def enviar_lote(mensagens: list[dict]) -> dict:
    try:
        resposta = requests.post(
            f"{CENTRAL_URL}/api/v1/ingestao/lote", params={"via": BORDA_ID}, json={"mensagens": mensagens}, timeout=15
        )
        resposta.raise_for_status()
        return resposta.json()
    except (requests.RequestException, ValueError) as exc:
        raise LinkIndisponivel(str(exc)) from exc


def informar_estado(fila: FilaLocal) -> None:
    try:
        requests.put(
            f"{CENTRAL_URL}/api/v1/bordas/{BORDA_ID}/estado",
            json={"fila_pendente": fila.contagem()["pendente"], "versao": VERSAO},
            timeout=5,
        ).raise_for_status()
    except requests.RequestException as exc:
        raise LinkIndisponivel(str(exc)) from exc


def carregar_regras_locais() -> list[Regra]:
    """Última versão recebida da central; sem ela, o arquivo padrão que vem com o nó."""
    origem = REGRAS_SINCRONIZADAS if REGRAS_SINCRONIZADAS.exists() else Path(REGRAS)
    return [Regra.de_dict(r) for r in json.loads(origem.read_text(encoding="utf-8"))]


def atualizar_regras(decisor: DecisorLocal) -> None:
    """Baixa as regras de escopo borda (RF-EDG-04) e troca as do decisor se mudaram."""
    try:
        resposta = requests.get(f"{CENTRAL_URL}/api/v1/bordas/{BORDA_ID}/regras", timeout=5)
        resposta.raise_for_status()
        brutas = resposta.json()
    except (requests.RequestException, ValueError) as exc:
        raise LinkIndisponivel(str(exc)) from exc
    novas = [Regra.de_dict(r) for r in brutas]
    if [r.para_dict() for r in novas] == [r.para_dict() for r in decisor.regras]:
        return
    decisor.regras = novas
    temporario = REGRAS_SINCRONIZADAS.with_suffix(".tmp")
    temporario.write_text(json.dumps(brutas, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(REGRAS_SINCRONIZADAS)  # troca atômica: queda de energia não deixa arquivo pela metade
    log.info("Regras locais atualizadas pela central: %s", ", ".join(r.nome for r in novas) or "nenhuma")


def laco_sincronizacao(
    fila: FilaLocal, sincronizador: Sincronizador, parar: threading.Event, decisor: DecisorLocal | None = None
) -> None:
    espera = SYNC_INTERVALO_S
    ultimo_estado = ultimas_regras = 0.0
    online = None
    while not parar.wait(espera):
        try:
            enviados = sincronizador.esvaziar()
            if time.time() - ultimo_estado >= ESTADO_INTERVALO_S:
                informar_estado(fila)
                ultimo_estado = time.time()
                fila.limpar_enviadas(RETENCAO_ENVIADAS_S)
            if decisor and (online is False or time.time() - ultimas_regras >= REGRAS_INTERVALO_S):
                atualizar_regras(decisor)  # também logo após reconectar
                ultimas_regras = time.time()
            if online is False:
                log.info("Link com a central restabelecido")
            if enviados:
                M_SINCRONIZADAS.inc(enviados)
                # fluxo normal (poucas por segundo) em DEBUG; esvaziar fila acumulada em INFO
                nivel = logging.INFO if enviados >= LOTE_MAX or online is False else logging.DEBUG
                log.log(nivel, "Sincronizadas %d mensagem(ns) com a central", enviados)
            online, espera = True, SYNC_INTERVALO_S
            M_LINK.set(1)
            M_ULTIMO_SYNC.set(time.time())
        except LinkIndisponivel as exc:
            if online is not False:
                log.warning("Central inacessível (%s) — guardando na fila local", exc)
            online, espera = False, min(SYNC_BACKOFF_MAX_S, max(SYNC_INTERVALO_S, espera * 2))
            M_LINK.set(0)
        M_PENDENTES.set(fila.contagem()["pendente"])


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────


def main() -> None:
    if not CHAVE:
        raise SystemExit(
            "BORDA_CHAVE não definida (gere com: python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        )
    Path(ARQUIVO_FILA).parent.mkdir(parents=True, exist_ok=True)
    fila = FilaLocal(ARQUIVO_FILA, CHAVE.encode())
    regras = carregar_regras_locais()
    decisor = DecisorLocal(regras, BORDA_ID, cooldown_s=ALERTA_COOLDOWN_SEG)

    log.info("═══════════════════════════════════════════════")
    log.info("  Nó de borda %s (fog) v%s", BORDA_ID, VERSAO)
    log.info("  Broker local : %s:%d", BROKER, PORT)
    log.info("  Central      : %s", CENTRAL_URL)
    log.info("  Fila         : %s (%d pendentes)", ARQUIVO_FILA, fila.contagem()["pendente"])
    log.info("  Regras locais: %s", ", ".join(r.nome for r in regras))
    log.info("═══════════════════════════════════════════════")

    start_http_server(METRICAS_PORTA)
    no = NoBorda(fila, decisor)
    no.iniciar()
    parar = threading.Event()
    try:
        laco_sincronizacao(fila, Sincronizador(fila, enviar_lote, LOTE_MAX), parar, decisor)
    except KeyboardInterrupt:
        log.info("Encerrando nó de borda…")
    finally:
        parar.set()
        no.cliente.loop_stop()
        fila.fechar()


if __name__ == "__main__":
    main()
