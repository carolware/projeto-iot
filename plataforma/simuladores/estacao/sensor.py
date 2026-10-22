"""
sensor.py — estações de detecção de incêndio simuladas (camada edge).

Fontes de dados:
  - Temperatura / Umidade → Open-Meteo API  (gratuita, sem chave de API)
  - Índice de fogo (0-100) → NASA FIRMS API  (chave em .env), focos de calor
                             convertidos em indicador qualitativo por zona.
  - Ruído de sensor       → variação gaussiana sobre os dados reais,
                            simulando imperfeições do hardware.

Publica no formato canônico v1.0, uma mensagem por leitura com as três
medidas, no tópico  brigada-cerrado/{zona}/incendio/est-{zona}-01/telemetria
do broker indicado em MQTT_BROKER: o broker local do nó de borda da região
(fog) ou o da central, para zonas sem borda.

Cada estação abre a própria conexão MQTT (TLS) com a própria credencial, como
um aparelho real: o broker só a deixa publicar nos tópicos dela. A senha de
cada estação simulada é derivada de SEGREDO_PROVISIONAMENTO (ver
nucleo/credenciais.py).

Pré-filtro na borda extrema (edge): só publica quando alguma medida variou
além do limiar ou quando venceu ENVIO_MAX_S — reduz banda e energia, como
pede o cenário rural (RNF-05).

Política de cache das APIs:
  - Open-Meteo  : atualizado a cada REFRESH_METEO_MIN (padrão 10 min)
  - NASA FIRMS  : atualizado a cada REFRESH_FIRMS_MIN (padrão 30 min)
"""

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # permite rodar como script

from nucleo.conexao_mqtt import configurar  # noqa: E402
from nucleo.credenciais import derivar_senha  # noqa: E402
from nucleo.geo import distancia_km  # noqa: E402
from nucleo.mensagem import Medida, nova_mensagem  # noqa: E402
from nucleo.topicos import montar_topico  # noqa: E402
from simuladores.zonas import DOMINIO, INQUILINO, TOPICO_MOCK, ZONAS, Zona, zonas_do_ambiente  # noqa: E402

load_dotenv()

# ──────────────────────────────────────────────
# Configurações gerais
# ──────────────────────────────────────────────

BROKER       = os.getenv("MQTT_BROKER", "localhost")
PORT         = int(os.getenv("MQTT_PORT", "8883"))
SEGREDO      = os.getenv("SEGREDO_PROVISIONAMENTO", "")
FIRMS_KEY    = os.getenv("FIRMS_MAP_KEY", "")
FIRMS_SOURCE = os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT")
FIRMS_DAYS   = int(os.getenv("FIRMS_DAYS", "1"))
FIRMS_RAIO   = float(os.getenv("FIRMS_RAIO_GRAUS", "0.25"))  # ~28 km para zonas rurais

REFRESH_METEO_MIN = int(os.getenv("SENSOR_REFRESH_METEO_MIN", "10"))
REFRESH_FIRMS_MIN = int(os.getenv("SENSOR_REFRESH_FIRMS_MIN", "30"))

CICLO_S     = float(os.getenv("SENSOR_CICLO_S", "3"))
ENVIO_MAX_S = float(os.getenv("SENSOR_ENVIO_MAX_S", "8"))  # < intervalo esperado do perfil (10 s)
LIMIARES    = {"temperatura": 1.0, "umidade": 2.0, "fumaca": 2.0}
UNIDADES    = {"temperatura": "Cel", "umidade": "%", "fumaca": "1"}

# ──────────────────────────────────────────────
# Mock manual de incêndio — sobrepõe uma zona com valores fixos de crise,
# ignorando Open-Meteo/FIRMS para ela. As demais zonas seguem com dados reais.
# Controlado em tempo real pelo tópico simulacao/mock (ver mock_incendio.py).
# ──────────────────────────────────────────────

MOCK_ZONA        = os.getenv("MOCK_ZONA_INCENDIO", "").strip().lower()
MOCK_TEMPERATURA = float(os.getenv("MOCK_TEMPERATURA", "42.0"))
MOCK_UMIDADE     = float(os.getenv("MOCK_UMIDADE", "10.0"))
MOCK_FUMACA      = float(os.getenv("MOCK_FUMACA", "93.0"))

# Coordenada opcional: vai no campo local.geo das mensagens da zona mockada
_mock_lat_env = os.getenv("MOCK_LAT", "").strip()
_mock_lon_env = os.getenv("MOCK_LON", "").strip()
MOCK_LAT: float | None = float(_mock_lat_env) if _mock_lat_env else None
MOCK_LON: float | None = float(_mock_lon_env) if _mock_lon_env else None

ZONAS_ATENDIDAS: list[Zona] = zonas_do_ambiente()

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
estado: dict[str, dict] = {z.slug: {"temperatura": 30.0, "umidade": 45.0, "fumaca": 5.0} for z in ZONAS_ATENDIDAS}

# Timestamps da última atualização de cada fonte (epoch)
_ts_meteo: dict[str, float] = {z.slug: 0.0 for z in ZONAS_ATENDIDAS}
_ts_firms: dict[str, float] = {z.slug: 0.0 for z in ZONAS_ATENDIDAS}

# Pré-filtro: última leitura publicada e quando
_ultimo_envio: dict[str, tuple[float, dict]] = {}
_contagem = {"publicadas": 0, "suprimidas": 0}


# ──────────────────────────────────────────────
# Open-Meteo — temperatura e umidade reais
# ──────────────────────────────────────────────

def buscar_meteorologia(zona: str, lat: float, lon: float) -> None:
    """
    Consulta a Open-Meteo API para obter temperatura (°C) e umidade relativa (%)
    atuais na coordenada da zona. Gratuita, sem autenticação.
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
        "temperatura": round(s["temperatura"] + random.gauss(0, 0.5), 1),
        "umidade":     round(max(5.0,  min(99.0, s["umidade"]  + random.gauss(0, 1.0))), 1),
        "fumaca":      round(max(0.0,  min(99.0, s["fumaca"]   + random.gauss(0, 0.8))), 1),
    }


def aplicar_mock_incendio(zona: str) -> None:
    """Sobrescreve a zona mockada com valores fixos de incêndio crítico."""
    estado[zona]["temperatura"] = MOCK_TEMPERATURA
    estado[zona]["umidade"]     = MOCK_UMIDADE
    estado[zona]["fumaca"]      = MOCK_FUMACA


def _coords_dentro_do_raio(zona: str, lat: float, lon: float) -> bool:
    """A coordenada customizada precisa cair dentro do raio de busca FIRMS
    da própria zona (o mesmo raio do círculo tracejado no mapa do front)."""
    z = ZONAS[zona]
    return distancia_km(z.lat, z.lon, lat, lon) <= FIRMS_RAIO * 111


def _forcar_refresh_real(zona: str) -> None:
    """Zera o cache da zona para que o próximo ciclo busque dados reais na hora."""
    if zona in _ts_meteo:
        _ts_meteo[zona] = 0.0
        _ts_firms[zona] = 0.0
        _ultimo_envio.pop(zona, None)  # publica já, sem esperar o pré-filtro


def tratar_comando_mock(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    """
    Liga/troca/desliga o mock de incêndio em tempo real via MQTT, sem
    precisar reiniciar o processo.

    Payload esperado (JSON):
      {"ativo": true, "zona": "mateiros", "temperatura": 42, "umidade": 10,
       "fumaca": 93, "lat": -10.1, "lon": -48.2}
      {"ativo": false}   → desativa o mock, zona volta a usar dados reais
    """
    global MOCK_ZONA, MOCK_TEMPERATURA, MOCK_UMIDADE, MOCK_FUMACA, MOCK_LAT, MOCK_LON

    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("[MOCK] comando com payload inválido: %r", msg.payload)
        return

    zona_anterior = MOCK_ZONA

    if not payload.get("ativo", True):
        MOCK_ZONA = ""
        MOCK_LAT = MOCK_LON = None
        if zona_anterior:
            _forcar_refresh_real(zona_anterior)
            log.warning("[MOCK] desativado (zona anterior: %s) — dados reais no próximo ciclo", zona_anterior)
        return

    zona = str(payload.get("zona", "")).strip().lower()
    if zona not in estado:
        return  # zona atendida por outro processo (outra região)

    MOCK_TEMPERATURA = float(payload.get("temperatura", MOCK_TEMPERATURA))
    MOCK_UMIDADE     = float(payload.get("umidade", MOCK_UMIDADE))
    MOCK_FUMACA      = float(payload.get("fumaca", MOCK_FUMACA))
    lat = payload.get("lat")
    lon = payload.get("lon")
    MOCK_LAT = MOCK_LON = None
    if lat is not None and lon is not None:
        lat_f, lon_f = float(lat), float(lon)
        if _coords_dentro_do_raio(zona, lat_f, lon_f):
            MOCK_LAT, MOCK_LON = lat_f, lon_f
        else:
            log.warning("[MOCK] coordenada (%.4f,%.4f) fora do raio de %s (%.1f km) — ignorada, zona mantém posição real.",
                        lat_f, lon_f, zona, FIRMS_RAIO * 111)
    MOCK_ZONA = zona
    aplicar_mock_incendio(zona)
    _ultimo_envio.pop(zona, None)
    if zona_anterior and zona_anterior != zona:
        _forcar_refresh_real(zona_anterior)  # zona antiga volta a dados reais no próximo ciclo

    coords_txt = f" @{MOCK_LAT},{MOCK_LON}" if MOCK_LAT is not None else ""
    log.warning("[MOCK] ativado → %s%s (temp=%.1f°C umid=%.1f%% fumaca=%.1f%%)",
                zona, coords_txt, MOCK_TEMPERATURA, MOCK_UMIDADE, MOCK_FUMACA)


def atualizar_fontes(zona: Zona) -> None:
    """
    Verifica se o cache das APIs expirou e, se sim, dispara nova consulta.
    Se a zona for a MOCK_ZONA, as APIs reais são ignoradas e os valores
    fixos de incêndio são aplicados em vez disso.
    """
    if zona.slug == MOCK_ZONA:
        aplicar_mock_incendio(zona.slug)
        return

    agora = time.time()
    if agora - _ts_meteo[zona.slug] >= REFRESH_METEO_MIN * 60:
        buscar_meteorologia(zona.slug, zona.lat, zona.lon)

    if agora - _ts_firms[zona.slug] >= REFRESH_FIRMS_MIN * 60:
        buscar_focos_firms(zona.slug, zona.lat, zona.lon)


# ──────────────────────────────────────────────
# MQTT — publicação no formato canônico
# ──────────────────────────────────────────────

def deve_publicar(zona: str, leitura: dict, agora: float) -> bool:
    """Pré-filtro do edge: variou além do limiar ou venceu o intervalo máximo."""
    anterior = _ultimo_envio.get(zona)
    if anterior is None or agora - anterior[0] >= ENVIO_MAX_S:
        return True
    return any(abs(leitura[g] - anterior[1][g]) >= LIMIARES[g] for g in LIMIARES)


def publicar_leitura(client: mqtt.Client, zona: Zona, leitura: dict) -> None:
    mockada = zona.slug == MOCK_ZONA
    lat, lon = (MOCK_LAT, MOCK_LON) if mockada and MOCK_LAT is not None else (zona.lat, zona.lon)
    mensagem = nova_mensagem(
        zona.estacao,
        "telemetria",
        medidas=[Medida(g, leitura[g], UNIDADES[g]) for g in ("temperatura", "umidade", "fumaca")],
        fonte="mock" if mockada else "open-meteo+firms-proxy",
        local={"site": zona.slug, "geo": {"type": "Point", "coordinates": [lon, lat]}},
    )
    topico = montar_topico(INQUILINO, zona.slug, DOMINIO, zona.estacao, "telemetria")
    client.publish(topico, json.dumps(mensagem, ensure_ascii=False), qos=1)

    tag = " [MOCK INCÊNDIO]" if mockada else ""
    print(
        f"[sensor] {zona.slug:20s} "
        f"temp={leitura['temperatura']:5.1f}c  "
        f"umid={leitura['umidade']:5.1f}%  "
        f"fumaca={leitura['fumaca']:5.1f}%{tag}"
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
    for zona in ZONAS_ATENDIDAS:
        if zona.slug == MOCK_ZONA:
            aplicar_mock_incendio(zona.slug)
            continue
        buscar_meteorologia(zona.slug, zona.lat, zona.lon)
        buscar_focos_firms(zona.slug, zona.lat, zona.lon)
        time.sleep(0.5)  # respeito ao rate limit das APIs
    log.info("Dados iniciais carregados. Iniciando publicacao MQTT.")


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────

def conectar(zona: Zona, controla_mock: bool) -> mqtt.Client:
    """Uma conexão por estação, autenticada com a credencial dela."""
    client = configurar(mqtt.Client(client_id=zona.estacao), zona.estacao, derivar_senha(SEGREDO, zona.estacao))

    def ao_conectar(cliente: mqtt.Client, userdata, flags, rc: int) -> None:
        if rc != 0:
            log.error("%s: conexão recusada pelo broker (rc=%d)", zona.estacao, rc)
            return
        if controla_mock:  # o canal de controle da simulação só precisa de um assinante por processo
            cliente.subscribe(TOPICO_MOCK, qos=1)
        log.info("%s conectada a %s:%d", zona.estacao, BROKER, PORT)

    client.on_connect = ao_conectar
    if controla_mock:
        client.message_callback_add(TOPICO_MOCK, tratar_comando_mock)
    client.connect_async(BROKER, PORT, keepalive=60)
    client.loop_start()
    return client


def main() -> None:
    print("=" * 55)
    print("  Estações SENTINELA — Dados REAIS (formato canônico)")
    print(f"  Broker  : {BROKER}:{PORT}")
    print(f"  Zonas   : {', '.join(z.slug for z in ZONAS_ATENDIDAS)}")
    print(f"  Meteo   : Open-Meteo (a cada {REFRESH_METEO_MIN} min)")
    print(f"  Fogo    : NASA FIRMS (a cada {REFRESH_FIRMS_MIN} min)")
    print(f"  Envio   : pré-filtro {LIMIARES} ou a cada {ENVIO_MAX_S:.0f} s")
    if MOCK_ZONA and MOCK_ZONA in estado:
        print(f"  MOCK    : {MOCK_ZONA} → incêndio simulado "
              f"(temp={MOCK_TEMPERATURA}°C umid={MOCK_UMIDADE}% fumaca={MOCK_FUMACA}%)")
    print("=" * 55)

    inicializar_dados()

    if not SEGREDO:
        raise SystemExit("SEGREDO_PROVISIONAMENTO não definido: as estações não têm credencial")
    clientes = {z.slug: conectar(z, controla_mock=(i == 0)) for i, z in enumerate(ZONAS_ATENDIDAS)}

    print("Ctrl+C para parar\n")

    ultimo_resumo = time.time()
    try:
        while True:
            for zona in ZONAS_ATENDIDAS:
                atualizar_fontes(zona)      # atualiza APIs se cache expirou
                leitura = leitura_atual(zona.slug)
                agora = time.time()
                if deve_publicar(zona.slug, leitura, agora):
                    publicar_leitura(clientes[zona.slug], zona, leitura)
                    _ultimo_envio[zona.slug] = (agora, leitura)
                    _contagem["publicadas"] += 1
                else:
                    _contagem["suprimidas"] += 1

            if time.time() - ultimo_resumo >= 60:
                total = sum(_contagem.values()) or 1
                log.info("Pré-filtro: %d publicadas, %d suprimidas (%.0f%% de banda economizada)",
                         _contagem["publicadas"], _contagem["suprimidas"], 100 * _contagem["suprimidas"] / total)
                ultimo_resumo = time.time()
            time.sleep(CICLO_S)
    except KeyboardInterrupt:
        print("\nEncerrando sensor...")
    finally:
        for client in clientes.values():
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    main()
