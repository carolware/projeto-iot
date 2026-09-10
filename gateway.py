"""
gateway.py — Gateway IoT: regras de risco + confirmação NASA FIRMS + comando MQTT

Fluxo:
  sensor → MQTT (topico sensor) → gateway → avalia risco → consulta FIRMS
                                           → publica alerta (topico atuador)

Tópicos subscritos :  sentinela-iot-2026-joao-carol/monitoramento-br/+/sensor/+
Tópicos publicados :  sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/atuador/alerta
"""

import csv
import io
import json
import logging
import math
import os
import threading
import time
import uuid
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
CLIENT_ID   = f"gateway-sentinela-{uuid.uuid4().hex[:10]}"
TOPIC_BASE  = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/monitoramento-br")
SUB_PATTERN = f"{TOPIC_BASE}/+/sensor/+"   # escuta todos os sensores
MOCK_COMANDO_TOPIC = f"{TOPIC_BASE}/comando/mock"
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
    "anapolis":          (-16.3267, -48.9530),  # Anápolis, GO
    "formosa":           (-15.5372, -47.3372),  # Formosa, GO
    "pirinopolis":       (-15.8558, -48.9597),  # Pirenópolis, GO
    "jaragua":           (-15.7529, -49.3344),  # Jaraguá, GO
    "sandolandia":       (-12.5408, -49.9192),  # Sandolândia, TO
    "novo-progresso":    ( -7.1261, -55.3853),  # Novo Progresso, PA (Amazônia)
    "mirador":           ( -6.3745, -44.3683),  # Mirador, MA — destaque MapBiomas 2025
    "mateiros":          (-10.5464, -46.4168),  # Mateiros, TO — destaque MapBiomas 2025
    "lagoa-da-confusao": (-10.7906, -49.6199),  # Lagoa da Confusão, TO — destaque MapBiomas 2025
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
        self.debounce_timer: threading.Timer | None = None

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

# Coordenada mockada em tempo real (via mock_incendio.py/sensor.py) que
# sobrepõe ZONA_COORDS na consulta FIRMS enquanto a zona estiver em mock.
MOCK_ZONA: str = ""
MOCK_LAT: float | None = None
MOCK_LON: float | None = None


def coords_da_zona(zona: str) -> tuple[float, float] | None:
    if zona == MOCK_ZONA and MOCK_LAT is not None and MOCK_LON is not None:
        return (MOCK_LAT, MOCK_LON)
    return ZONA_COORDS.get(zona)


def on_comando_mock(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    """Espelha o comando de mock do sensor.py só para saber onde consultar o
    FIRMS — quem decide os valores de temperatura/umidade/fumaça é o sensor."""
    global MOCK_ZONA, MOCK_LAT, MOCK_LON

    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    if not payload.get("ativo", True):
        MOCK_ZONA = ""
        MOCK_LAT = MOCK_LON = None
        return

    zona = str(payload.get("zona", "")).strip().lower()
    if zona not in ZONA_COORDS:
        return

    lat = payload.get("lat")
    lon = payload.get("lon")
    MOCK_ZONA = zona
    if lat is not None and lon is not None:
        lat_f, lon_f = float(lat), float(lon)
        lat_real, lon_real = ZONA_COORDS[zona]
        raio_km = FIRMS_RAIO * 111
        if _distancia_km(lat_real, lon_real, lat_f, lon_f) <= raio_km:
            MOCK_LAT, MOCK_LON = lat_f, lon_f
            log.warning("[MOCK] FIRMS de %s passa a consultar @%.4f,%.4f", zona, MOCK_LAT, MOCK_LON)
        else:
            MOCK_LAT = MOCK_LON = None
            log.warning("[MOCK] coordenada (%.4f,%.4f) fora do raio de %s (%.1f km) — ignorada, FIRMS mantém posição real.",
                        lat_f, lon_f, zona, raio_km)
    else:
        MOCK_LAT = MOCK_LON = None


# ──────────────────────────────────────────────
# Integração NASA FIRMS
# ──────────────────────────────────────────────

def _distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    raio_terra = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return raio_terra * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _resultado_firms_vazio(erro: str | None = None) -> dict:
    resultado = {
        "focos": 0,
        "confirmado": False,
        "frp_max": 0,
        "distancia_km": None,
        "raio_km": round(FIRMS_RAIO * 111, 1),
        "janela_horas": FIRMS_DAYS * 24,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "hotspots": [],
    }
    if erro:
        resultado["erro"] = erro
    return resultado


def _resultado_firms_mock(lat: float, lon: float) -> dict:
    """Fabrica uma confirmação de satélite na coordenada exata do mock —
    dispensa consultar a API real, que não teria foco nenhum ali."""
    ts = datetime.now(timezone.utc).isoformat()
    hotspot = {
        "id": f"mock-{lat}-{lon}",
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "frp": 145.0,
        "confidence": "alta (mock)",
        "satellite": "MOCK",
        "instrument": "MOCK",
        "acquired_at": ts,
        "daynight": "D",
        "distance_km": 0.0,
    }
    return {
        "focos": 1,
        "confirmado": True,
        "frp_max": hotspot["frp"],
        "distancia_km": 0.0,
        "raio_km": round(FIRMS_RAIO * 111, 1),
        "janela_horas": FIRMS_DAYS * 24,
        "atualizado_em": ts,
        "hotspots": [hotspot],
    }


def consultar_firms(lat: float, lon: float) -> dict:
    """Retorna os focos FIRMS e seus metadados geográficos na área da zona."""
    if not FIRMS_KEY:
        log.warning("FIRMS_MAP_KEY não configurada; pulando consulta satélite.")
        return _resultado_firms_vazio("FIRMS_MAP_KEY não configurada")

    west = round(lon - FIRMS_RAIO, 4)
    east = round(lon + FIRMS_RAIO, 4)
    south = round(lat - FIRMS_RAIO, 4)
    north = round(lat + FIRMS_RAIO, 4)
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv"
        f"/{FIRMS_KEY}/{FIRMS_SOURCE}/{west},{south},{east},{north}/{FIRMS_DAYS}"
    )

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Falha na consulta FIRMS: %s", exc)
        return _resultado_firms_vazio(str(exc))

    hotspots: list[dict] = []
    try:
        registros = csv.DictReader(io.StringIO(resp.text))
        for indice, registro in enumerate(registros):
            try:
                flat = float(registro["latitude"])
                flon = float(registro["longitude"])
                frp = float(registro.get("frp") or 0)
            except (KeyError, TypeError, ValueError):
                continue

            data = registro.get("acq_date", "")
            hora = (registro.get("acq_time") or "").zfill(4)
            adquirido_em = f"{data}T{hora[:2]}:{hora[2:]}:00Z" if data and hora else None
            distancia = round(_distancia_km(lat, lon, flat, flon), 2)
            hotspots.append({
                "id": f"{registro.get('satellite', 'sat')}-{data}-{hora}-{indice}",
                "latitude": round(flat, 5),
                "longitude": round(flon, 5),
                "frp": round(frp, 1),
                "confidence": registro.get("confidence") or "n/d",
                "satellite": registro.get("satellite") or "n/d",
                "instrument": registro.get("instrument") or "n/d",
                "acquired_at": adquirido_em,
                "daynight": registro.get("daynight") or "n/d",
                "distance_km": distancia,
            })
    except csv.Error as exc:
        log.error("CSV FIRMS inválido: %s", exc)
        return _resultado_firms_vazio(str(exc))

    hotspots.sort(key=lambda foco: foco["frp"], reverse=True)
    resultado = _resultado_firms_vazio()
    resultado.update({
        "focos": len(hotspots),
        "confirmado": bool(hotspots),
        "frp_max": max((foco["frp"] for foco in hotspots), default=0),
        "distancia_km": min((foco["distance_km"] for foco in hotspots), default=None),
        "hotspots": hotspots[:100],
    })
    return resultado


def publicar_status_firms(client: mqtt.Client, zona: str, resultado: dict) -> None:
    payload = {"zona": zona, **resultado}
    topic = f"{TOPIC_BASE}/{zona}/satelite/firms"
    client.publish(topic, json.dumps(payload), qos=1, retain=True)


# ──────────────────────────────────────────────
# Lógica principal do gateway
# ──────────────────────────────────────────────

# sensor.py publica temperatura/umidade/fumaça como 3 mensagens MQTT
# separadas por zona a cada ciclo. Avaliar o risco a cada mensagem individual
# significa fazê-lo com leitura parcial (1 valor novo + 2 do ciclo anterior)
# por uma fração de segundo — o suficiente pra cair fora da faixa crítica,
# resetar risco_anterior e, na mensagem seguinte, disparar uma "reescalada"
# falsa (alerta repetindo a cada ciclo em vez de respeitar o cooldown).
# Por isso agrupamos as 3 mensagens do mesmo ciclo antes de avaliar.
PROCESSAMENTO_DEBOUNCE_SEG = 0.3


def agendar_avaliacao(client: mqtt.Client, estado: EstadoZona) -> None:
    if estado.debounce_timer is not None:
        estado.debounce_timer.cancel()
    estado.debounce_timer = threading.Timer(PROCESSAMENTO_DEBOUNCE_SEG, processar_zona, args=(client, estado))
    estado.debounce_timer.daemon = True
    estado.debounce_timer.start()


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

    # ── Varredura orbital periódica para todas as zonas ──
    firms_resultado: dict = {}
    agora = time.time()
    if agora - estado.ultimo_firms_ts >= FIRMS_COOLDOWN_SEG:
        estado.ultimo_firms_ts = agora
        coords = coords_da_zona(estado.zona)
        mockada = estado.zona == MOCK_ZONA and MOCK_LAT is not None and MOCK_LON is not None
        if coords:
            if mockada:
                log.warning("[MOCK] fabricando foco FIRMS para %s @ %.4f,%.4f", estado.zona, *coords)
                firms_resultado = _resultado_firms_mock(*coords)
            else:
                log.info("Consultando NASA FIRMS para %s @ %.4f,%.4f …", estado.zona, *coords)
                firms_resultado = consultar_firms(*coords)
            estado.ultimo_firms_resultado = firms_resultado
            estado.firms_confirmado = firms_resultado.get("confirmado", False)
            publicar_status_firms(client, estado.zona, firms_resultado)
            if estado.firms_confirmado:
                log.warning(
                    "FIRMS confirmou %d foco(s) perto de %s (FRP=%.0f MW, dist=%.1f km)",
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
        client.subscribe(MOCK_COMANDO_TOPIC, qos=1)
        log.info("Subscrito em '%s' e '%s'", SUB_PATTERN, MOCK_COMANDO_TOPIC)
    else:
        log.error("Falha na conexão com broker; rc=%d", rc)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    """
    Decodifica o tópico  sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/sensor/{tipo}
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
        agendar_avaliacao(client, zonas[zona])


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
    client.message_callback_add(MOCK_COMANDO_TOPIC, on_comando_mock)

    client.connect(BROKER, PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Encerrando gateway…")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
