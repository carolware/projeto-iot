"""
gateway.py — Gateway IoT: regras de risco + confirmação NASA FIRMS + comando MQTT

Fluxo:
  sensor → MQTT (topico sensor) → gateway → avalia risco → consulta FIRMS
                                           → publica alerta (topico atuador)

Tópicos subscritos :  minha-equipe/cidade-incendio/+/sensor/+
Tópicos publicados :  minha-equipe/cidade-incendio/{zona}/atuador/alerta
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Literal

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Configurações gerais
# ──────────────────────────────────────────────

BROKER      = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT        = int(os.getenv("MQTT_PORT", "1883"))
CLIENT_ID   = "gateway-incendio-florestal"
TOPIC_BASE  = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/chapada-veadeiros")
SUB_PATTERN = f"{TOPIC_BASE}/+/sensor/+"   # escuta todos os sensores
FIRMS_KEY   = os.getenv("FIRMS_MAP_KEY", "")
FIRMS_SOURCE = os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT")  # ou MODIS_NRT
FIRMS_DAYS  = int(os.getenv("FIRMS_DAYS", "1"))             # janela temporal (dias)
FIRMS_RAIO  = float(os.getenv("FIRMS_RAIO_GRAUS", "0.15"))  # ~16 km

# Cooldowns para limitar consultas externas e alertas repetidos
FIRMS_COOLDOWN_SEG = int(os.getenv("FIRMS_COOLDOWN_SEG", "60"))
ALERTA_COOLDOWN_SEG = int(os.getenv("ALERTA_COOLDOWN_SEG", "30"))

# ──────────────────────────────────────────────
# Mapeamento: zona → coordenadas geográficas
# Ajuste conforme a localização real dos sensores
# ──────────────────────────────────────────────

ZONA_COORDS: dict[str, tuple[float, float]] = {
    "alto-paraiso":   (-14.1330, -47.5170),
    "vila-sao-jorge": (-14.1775, -47.8140),
    "cavalcante":     (-13.7975, -47.4583),
    "colinas-do-sul": (-14.1528, -48.0760),
}

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gateway] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gateway")

# ──────────────────────────────────────────────
# Tipos / constantes de risco
# ──────────────────────────────────────────────

Risco = Literal["baixo", "medio", "alto", "critico"]

LIMIARES = {
    # (smoke_min, humidity_max, temp_min) → risco
    "critico": {"fumaca_min": 85, "umidade_max": 15},
    "alto":    {"fumaca_min": 60, "umidade_max": 25},
    "medio":   {"fumaca_min": 40, "umidade_max": 35, "temp_min": 33},
}


def avaliar_risco(fumaca: float, umidade: float, temperatura: float) -> Risco:
    """
    Regras de inferência baseadas nos limiares físicos do ambiente.

    - Crítico : fumaça >= 85 %  E  umidade < 15 %
    - Alto    : fumaça >= 60 %  E  umidade < 25 %
    - Médio   : fumaça >= 40 %  OU umidade < 35 %  OU temperatura > 33 °C
    - Baixo   : demais casos
    """
    if fumaca >= 85 and umidade < 15:
        return "critico"
    if fumaca >= 60 and umidade < 25:
        return "alto"
    if fumaca >= 40 or umidade < 35 or temperatura > 33:
        return "medio"
    return "baixo"


# ──────────────────────────────────────────────
# Estado por zona (thread-safe com Lock)
# ──────────────────────────────────────────────

class EstadoZona:
    def __init__(self, zona: str):
        self.zona = zona
        self.temperatura: float | None  = None
        self.umidade:     float | None  = None
        self.fumaca:      float | None  = None
        self.risco_anterior: Risco      = "baixo"
        self.ultimo_firms_ts: float     = 0.0   # epoch da última consulta FIRMS
        self.ultimo_alerta_ts: float    = 0.0
        self.firms_confirmado: bool     = False
        self.ultimo_firms_resultado: dict = {}
        self.lock = threading.Lock()

    def atualizar(self, campo: str, valor: float) -> None:
        with self.lock:
            setattr(self, campo, valor)

    def leitura_completa(self) -> bool:
        return all(v is not None for v in (self.temperatura, self.umidade, self.fumaca))

    def snapshot(self) -> tuple[float, float, float]:
        """Retorna (temperatura, umidade, fumaca) de forma thread-safe."""
        with self.lock:
            return (self.temperatura, self.umidade, self.fumaca)  # type: ignore[return-value]


zonas: dict[str, EstadoZona] = {z: EstadoZona(z) for z in ZONA_COORDS}


# ──────────────────────────────────────────────
# Integração NASA FIRMS
# ──────────────────────────────────────────────

def consultar_firms(lat: float, lon: float) -> dict:
    """
    Consulta a API NASA FIRMS (Fire Information for Resource Management System)
    para detectar focos de calor ativos numa caixa delimitadora ao redor da
    coordenada fornecida.

    Endpoint utilizado:
      https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}

    Parâmetros do bbox: west,south,east,north (em graus decimais)

    Retorna um dicionário com:
      focos        — número de registros VIIRS/MODIS na área
      confirmado   — True se houver pelo menos 1 foco
      frp_max      — Fire Radiative Power máximo (W/m²), 0 se nenhum foco
      distancia_km — menor distância estimada ao sensor (aproximação euclidiana)
    """
    if not FIRMS_KEY:
        log.warning("FIRMS_MAP_KEY não configurada; pulando consulta satélite.")
        return {"focos": 0, "confirmado": False, "frp_max": 0, "distancia_km": None}

    west  = round(lon - FIRMS_RAIO, 4)
    east  = round(lon + FIRMS_RAIO, 4)
    south = round(lat - FIRMS_RAIO, 4)
    north = round(lat + FIRMS_RAIO, 4)
    url   = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv"
        f"/{FIRMS_KEY}/{FIRMS_SOURCE}/{west},{south},{east},{north}/{FIRMS_DAYS}"
    )

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Falha na consulta FIRMS: %s", exc)
        return {"focos": 0, "confirmado": False, "frp_max": 0, "distancia_km": None, "erro": str(exc)}

    linhas = [l for l in resp.text.strip().splitlines() if l and not l.startswith("latitude")]

    if not linhas:
        return {"focos": 0, "confirmado": False, "frp_max": 0, "distancia_km": None}

    # Parse CSV básico para extrair FRP (Fire Radiative Power)
    frp_max     = 0.0
    dist_min_km = None
    header      = resp.text.strip().splitlines()[0].lower().split(",")

    try:
        idx_lat = header.index("latitude")
        idx_lon = header.index("longitude")
        idx_frp = header.index("frp") if "frp" in header else None
    except ValueError:
        idx_lat = idx_lon = idx_frp = None

    for linha in linhas:
        cols = linha.split(",")
        if idx_frp is not None and len(cols) > idx_frp:
            try:
                frp = float(cols[idx_frp])
                frp_max = max(frp_max, frp)
            except ValueError:
                pass
        if idx_lat is not None and idx_lon is not None and len(cols) > max(idx_lat, idx_lon):
            try:
                flat = float(cols[idx_lat])
                flon = float(cols[idx_lon])
                # distância euclidiana aproximada em km (1° ≈ 111 km)
                dist = ((flat - lat) ** 2 + (flon - lon) ** 2) ** 0.5 * 111
                dist_min_km = round(dist, 2) if dist_min_km is None else min(dist_min_km, round(dist, 2))
            except ValueError:
                pass

    return {
        "focos":        len(linhas),
        "confirmado":   True,
        "frp_max":      round(frp_max, 1),
        "distancia_km": dist_min_km,
    }


# ──────────────────────────────────────────────
# Lógica principal do gateway
# ──────────────────────────────────────────────

def processar_zona(client: mqtt.Client, estado: EstadoZona) -> None:
    """
    Avalia o risco de uma zona e, se necessário, consulta o satélite e publica
    um comando de alerta para o atuador.
    """
    if not estado.leitura_completa():
        return

    temp, umid, fumaca = estado.snapshot()
    risco = avaliar_risco(fumaca, umid, temp)

    ts = datetime.now(timezone.utc).isoformat()
    log.info(
        "%-22s temp=%5.1f°C  umid=%5.1f%%  fumaca=%5.1f%%  → risco=%s",
        estado.zona, temp, umid, fumaca, risco.upper(),
    )

    # ── Verificação via satélite (se risco ≥ médio e cooldown expirado) ──
    firms_resultado: dict = {}
    if risco in ("medio", "alto", "critico"):
        agora = time.time()
        if agora - estado.ultimo_firms_ts >= FIRMS_COOLDOWN_SEG:
            estado.ultimo_firms_ts = agora
            coords = ZONA_COORDS.get(estado.zona)
            if coords:
                log.info("Consultando NASA FIRMS para %s @ %.4f,%.4f …", estado.zona, *coords)
                firms_resultado = consultar_firms(*coords)
                estado.ultimo_firms_resultado = firms_resultado
                estado.firms_confirmado = firms_resultado.get("confirmado", False)
                if estado.firms_confirmado:
                    log.warning(
                        "FIRMS confirmou %d foco(s) perto de %s (FRP=%.0f W/m², dist=%.1f km)",
                        firms_resultado["focos"],
                        estado.zona,
                        firms_resultado.get("frp_max", 0),
                        firms_resultado.get("distancia_km") or 0,
                    )
                else:
                    log.info("FIRMS: nenhum foco ativo perto de %s.", estado.zona)

    # ── Publicar uma vez na escalada e repetir somente após o cooldown ──
    agora = time.time()
    escalou_para_critico = risco == "critico" and estado.risco_anterior != "critico"
    cooldown_expirou = agora - estado.ultimo_alerta_ts >= ALERTA_COOLDOWN_SEG
    if risco in ("alto", "critico") and (escalou_para_critico or cooldown_expirou):
        resultado = firms_resultado or estado.ultimo_firms_resultado
        _publicar_alerta(client, estado, risco, temp, umid, fumaca, ts, resultado)
        estado.ultimo_alerta_ts = agora

    # Escalada: se risco subiu, emitir log de mudança de estado
    if risco != estado.risco_anterior:
        log.info("Mudança de estado: %s  %s → %s", estado.zona, estado.risco_anterior.upper(), risco.upper())
        estado.risco_anterior = risco


def _publicar_alerta(
    client: mqtt.Client,
    estado: EstadoZona,
    risco: Risco,
    temp: float,
    umid: float,
    fumaca: float,
    ts: str,
    firms: dict,
) -> None:
    """Serializa e publica o payload de alerta no tópico do atuador."""
    payload = {
        "zona":             estado.zona,
        "risco":            risco,
        "temperatura":      round(temp, 1),
        "umidade":          round(umid, 1),
        "fumaca":           round(fumaca, 1),
        "timestamp":        ts,
        "firms_confirmado": estado.firms_confirmado,
        "firms_focos":      firms.get("focos", 0),
        "firms_frp_max":    firms.get("frp_max", 0),
        "firms_dist_km":    firms.get("distancia_km"),
        "acao":             "acionar_brigada" if risco == "critico" else "reforcar_monitoramento",
    }

    topic = f"{TOPIC_BASE}/{estado.zona}/atuador/alerta"
    client.publish(topic, json.dumps(payload), qos=1, retain=False)

    log.warning(
        "ALERTA publicado → %s  risco=%s  acao=%s",
        topic, risco.upper(), payload["acao"],
    )


# ──────────────────────────────────────────────
# Callbacks MQTT
# ──────────────────────────────────────────────

def on_connect(client: mqtt.Client, userdata, flags, rc: int) -> None:
    if rc == 0:
        log.info("Conectado ao broker %s:%d", BROKER, PORT)
        client.subscribe(SUB_PATTERN, qos=0)
        log.info("Subscrito em '%s'", SUB_PATTERN)
    else:
        log.error("Falha na conexão com broker; rc=%d", rc)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    """
    Decodifica o tópico  minha-equipe/cidade-incendio/{zona}/sensor/{tipo}
    e atualiza o estado da zona correspondente.
    """
    parts = msg.topic.split("/")
    # Esperamos exatamente: [prefix_0, prefix_1, zona, "sensor", tipo]
    if len(parts) < 5 or parts[-2] != "sensor":
        return

    zona = parts[-3]
    tipo = parts[-1]  # temperatura | umidade | fumaca

    if zona not in zonas:
        # zona nova: criar entry dinamicamente
        zonas[zona] = EstadoZona(zona)
        if zona not in ZONA_COORDS:
            log.warning("Zona '%s' sem coordenadas; consulta FIRMS desabilitada para ela.", zona)

    try:
        payload = json.loads(msg.payload)
        valor   = float(payload.get("valor", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        log.debug("Payload inválido em %s: %s", msg.topic, msg.payload)
        return

    campo_map = {"temperatura": "temperatura", "umidade": "umidade", "fumaca": "fumaca"}
    if tipo in campo_map:
        zonas[zona].atualizar(campo_map[tipo], valor)
        processar_zona(client, zonas[zona])


def on_disconnect(client: mqtt.Client, userdata, rc: int) -> None:
    if rc != 0:
        log.warning("Desconexão inesperada (rc=%d); reconectando…", rc)


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────

def main() -> None:
    log.info("═══════════════════════════════════════════════")
    log.info("  Gateway SENTINELA — Monitoramento de Incêndio")
    log.info("  Broker : %s:%d", BROKER, PORT)
    log.info("  FIRMS  : %s", "configurado" if FIRMS_KEY else "DESABILITADO (sem chave)")
    log.info("═══════════════════════════════════════════════")

    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    client.connect(BROKER, PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Encerrando gateway…")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
