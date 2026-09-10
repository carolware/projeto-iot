"""
sensor.py — Sensor IoT com dados REAIS

Fontes de dados:
  - Temperatura / Umidade → Open-Meteo API  (gratuita, sem chave de API)
                            https://api.open-meteo.com
  - Índice de fogo (0-100) → NASA FIRMS API  (chave em .env)
                             Focos de calor detectados por satélite VIIRS/MODIS
                             convertidos em indicador qualitativo por zona.
  - Ruído de sensor       → variação gaussiana (±1 °C, ±2 % umidade) sobre os
                            dados reais, simulando imperfeições do hardware.

Política de cache:
  - Open-Meteo  : atualizado a cada REFRESH_METEO_MIN (padrão 10 min)
  - NASA FIRMS  : atualizado a cada REFRESH_FIRMS_MIN (padrão 30 min)
  Entre atualizações os valores anteriores + ruído são publicados, garantindo
  alta taxa de mensagens MQTT sem consumir cota desnecessária das APIs.

Zonas monitoradas (Goiás · Tocantins · Pará · Maranhão):
  anapolis | formosa | pirinopolis | jaragua | sandolandia |
  novo-progresso | mirador | mateiros | lagoa-da-confusao
"""

import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Configurações gerais
# ──────────────────────────────────────────────

BROKER       = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT         = int(os.getenv("MQTT_PORT", "1883"))
CLIENT_ID    = f"sensor-sentinela-{uuid.uuid4().hex[:10]}"
TOPIC_PREFIX = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/monitoramento-br")
FIRMS_KEY    = os.getenv("FIRMS_MAP_KEY", "")
FIRMS_SOURCE = os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT")
FIRMS_DAYS   = int(os.getenv("FIRMS_DAYS", "1"))
FIRMS_RAIO   = float(os.getenv("FIRMS_RAIO_GRAUS", "0.25"))  # ~28 km para zonas rurais

REFRESH_METEO_MIN = int(os.getenv("SENSOR_REFRESH_METEO_MIN", "10"))
REFRESH_FIRMS_MIN = int(os.getenv("SENSOR_REFRESH_FIRMS_MIN", "30"))

# ──────────────────────────────────────────────
# Mapeamento: zona → coordenadas geográficas
# ──────────────────────────────────────────────

ZONA_COORDS: dict[str, tuple[float, float]] = {
    "anapolis":          (-16.3267, -48.9530),  # Anápolis, GO
    "formosa":           (-15.5372, -47.3372),  # Formosa, GO
    "pirinopolis":       (-15.8558, -48.9597),  # Pirenópolis, GO
    "jaragua":           (-15.7529, -49.3344),  # Jaraguá, GO
    "sandolandia":       (-12.5408, -49.9192),  # Sandolândia, TO
    "novo-progresso":    ( -7.1261, -55.3853),  # Novo Progresso, PA
    "mirador":           ( -6.3745, -44.3683),  # Mirador, MA
    "mateiros":          (-10.5464, -46.4168),  # Mateiros, TO
    "lagoa-da-confusao": (-10.7906, -49.6199),  # Lagoa da Confusão, TO
}

ZONAS = list(ZONA_COORDS.keys())

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sensor]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sensor")

# ──────────────────────────────────────────────
# Cache de dados reais por zona
# ──────────────────────────────────────────────

# Valores atuais (inicializados com fallback até a 1ª consulta real)
_FALLBACK: dict[str, dict] = {
    zona: {"temperatura": 30.0, "umidade": 45.0, "fumaca": 5.0}
    for zona in ZONAS
}

estado: dict[str, dict] = {z: dict(v) for z, v in _FALLBACK.items()}

# Timestamps da última atualização de cada fonte (epoch)
_ts_meteo: dict[str, float] = {z: 0.0 for z in ZONAS}
_ts_firms: dict[str, float] = {z: 0.0 for z in ZONAS}


# ──────────────────────────────────────────────
# Open-Meteo — temperatura e umidade reais
# ──────────────────────────────────────────────

def buscar_meteorologia(zona: str, lat: float, lon: float) -> None:
    """
    Consulta a Open-Meteo API para obter temperatura (°C) e umidade relativa (%)
    atuais na coordenada da zona. Gratuita, sem autenticação.

    Endpoint: https://api.open-meteo.com/v1/forecast
    Parâmetros: current=temperature_2m,relative_humidity_2m
    """
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m"
        "&timezone=America%2FSao_Paulo"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("current", {})
        temp = float(data.get("temperature_2m", estado[zona]["temperatura"]))
        umid = float(data.get("relative_humidity_2m", estado[zona]["umidade"]))

        estado[zona]["temperatura"] = round(temp, 1)
        estado[zona]["umidade"]     = round(umid,  1)
        _ts_meteo[zona] = time.time()

        log.info("%-20s [Open-Meteo] temp=%.1f°C  umid=%.0f%%", zona, temp, umid)

    except Exception as exc:
        log.warning("%-20s [Open-Meteo] falha: %s — usando valor anterior", zona, exc)


# ──────────────────────────────────────────────
# NASA FIRMS — índice de fumaça derivado de focos de calor
# ──────────────────────────────────────────────

def _focos_para_fumaca(focos: int, frp_max: float) -> float:
    """
    Converte número de focos FIRMS e FRP máximo em índice de fumaça (0-100 %).

    Metodologia (proxy qualitativo):
      0 focos                   → base sazonal baixa (2-12 %)
      1-3 focos / FRP baixo     → fumaça leve        (15-40 %)
      3-8 focos / FRP médio     → fumaça moderada    (40-65 %)
      9+ focos / FRP alto       → fumaça intensa     (65-92 %)
    O FRP (Fire Radiative Power, MW) reflete a intensidade do fogo.
    """
    if focos == 0:
        return random.uniform(2, 12)
    if focos <= 3 and frp_max < 50:
        return random.uniform(15, 40)
    if focos <= 8 and frp_max < 150:
        return random.uniform(40, 65)
    return min(92.0, random.uniform(65, 85) + frp_max * 0.03)


def buscar_focos_firms(zona: str, lat: float, lon: float) -> None:
    """
    Consulta a NASA FIRMS API para detectar focos de calor ativos na caixa
    delimitadora em torno das coordenadas da zona e converte o resultado em
    um índice de fumaça (0-100 %).

    Caso a chave FIRMS não esteja configurada, o índice de fumaça é mantido
    com base sazonal + ruído.
    """
    if not FIRMS_KEY:
        log.warning("%-20s [FIRMS] FIRMS_MAP_KEY não configurada; fumaça estimada.", zona)
        estado[zona]["fumaca"] = round(random.uniform(2, 15), 1)
        _ts_firms[zona] = time.time()
        return

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
        linhas = [l for l in resp.text.strip().splitlines()
                  if l and not l.startswith("latitude")]

        frp_max = 0.0
        header  = resp.text.strip().splitlines()[0].lower().split(",") if linhas else []
        if linhas and "frp" in header:
            idx_frp = header.index("frp")
            for linha in linhas:
                try:
                    frp_max = max(frp_max, float(linha.split(",")[idx_frp]))
                except ValueError:
                    pass

        fumaca = _focos_para_fumaca(len(linhas), frp_max)
        estado[zona]["fumaca"] = round(fumaca, 1)
        _ts_firms[zona] = time.time()

        if linhas:
            log.info("%-20s [FIRMS] %d foco(s) detectado(s) · FRP_max=%.0f MW → fumaca=%.1f%%",
                     zona, len(linhas), frp_max, fumaca)
        else:
            log.info("%-20s [FIRMS] nenhum foco ativo · fumaca=%.1f%%", zona, fumaca)

    except Exception as exc:
        log.warning("%-20s [FIRMS] falha: %s — usando valor anterior", zona, exc)


# ──────────────────────────────────────────────
# Leitura do sensor (dados reais + ruído)
# ──────────────────────────────────────────────

def leitura_atual(zona: str) -> dict:
    """
    Retorna os valores atuais do sensor para a zona, adicionando ruído
    gaussiano que simula as imprecisões de hardware de um sensor real
    (termistor ±1 °C, sensor capacitivo de umidade ±2 %, etc.).
    """
    s = estado[zona]
    return {
        "zona":        zona,
        # Ruído de sensor sobre dado real
        "temperatura": round(s["temperatura"] + random.gauss(0, 0.5), 1),
        "umidade":     round(max(5.0,  min(99.0, s["umidade"]  + random.gauss(0, 1.0))), 1),
        "fumaca":      round(max(0.0,  min(99.0, s["fumaca"]   + random.gauss(0, 0.8))), 1),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


def atualizar_fontes(zona: str) -> None:
    """
    Verifica se o cache das APIs expirou e, se sim, dispara nova consulta.
    Chamada a cada ciclo de publicação MQTT.
    """
    agora = time.time()
    lat, lon = ZONA_COORDS[zona]

    if agora - _ts_meteo[zona] >= REFRESH_METEO_MIN * 60:
        buscar_meteorologia(zona, lat, lon)

    if agora - _ts_firms[zona] >= REFRESH_FIRMS_MIN * 60:
        buscar_focos_firms(zona, lat, lon)


# ──────────────────────────────────────────────
# MQTT — publicação dos dados
# ──────────────────────────────────────────────

def publicar_leitura(client: mqtt.Client, leitura: dict) -> None:
    """Publica temperatura, umidade e fumaça em tópicos MQTT separados."""
    zona = leitura["zona"]
    ts   = leitura["timestamp"]

    client.publish(f"{TOPIC_PREFIX}/{zona}/sensor/temperatura",
                   json.dumps({"valor": leitura["temperatura"], "timestamp": ts, "fonte": "open-meteo"}))
    client.publish(f"{TOPIC_PREFIX}/{zona}/sensor/umidade",
                   json.dumps({"valor": leitura["umidade"],     "timestamp": ts, "fonte": "open-meteo"}))
    client.publish(f"{TOPIC_PREFIX}/{zona}/sensor/fumaca",
                   json.dumps({"valor": leitura["fumaca"],      "timestamp": ts, "fonte": "firms-proxy"}))

    print(
        f"[sensor] {zona:20s} "
        f"temp={leitura['temperatura']:5.1f}c  "
        f"umid={leitura['umidade']:5.1f}%  "
        f"fumaca={leitura['fumaca']:5.1f}%"
    )


# ──────────────────────────────────────────────
# Inicialização das APIs antes do loop MQTT
# ──────────────────────────────────────────────

def inicializar_dados() -> None:
    """
    Busca os dados reais de todas as zonas antes de iniciar o loop MQTT,
    garantindo que os primeiros valores publicados sejam reais.
    """
    log.info("Buscando dados reais das APIs (Open-Meteo + NASA FIRMS)...")
    for zona in ZONAS:
        lat, lon = ZONA_COORDS[zona]
        buscar_meteorologia(zona, lat, lon)
        buscar_focos_firms(zona, lat, lon)
        time.sleep(0.5)  # respeito ao rate limit das APIs
    log.info("Dados iniciais carregados. Iniciando publicacao MQTT.")


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────

def main() -> None:
    print("=" * 55)
    print("  Sensor SENTINELA — Dados REAIS")
    print(f"  Broker  : {BROKER}:{PORT}")
    print(f"  Meteo   : Open-Meteo (a cada {REFRESH_METEO_MIN} min)")
    print(f"  Fogo    : NASA FIRMS (a cada {REFRESH_FIRMS_MIN} min)")
    print("=" * 55)

    inicializar_dados()

    client = mqtt.Client(client_id=CLIENT_ID)
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_start()

    print(f"\nPublicando em '{TOPIC_PREFIX}/*'")
    print("Ctrl+C para parar\n")

    try:
        while True:
            for zona in ZONAS:
                atualizar_fontes(zona)      # atualiza APIs se cache expirou
                leitura = leitura_atual(zona)
                publicar_leitura(client, leitura)

            time.sleep(3)
    except KeyboardInterrupt:
        print("\nEncerrando sensor...")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
