"""
mock_incendio.py — CLI para ligar/desligar o mock de incêndio do sensor.py
em tempo real, publicando no mesmo tópico de comando MQTT que ele assina
({MQTT_TOPIC_BASE}/comando/mock). Não precisa reiniciar o sensor.py.

Uso:
  python mock_incendio.py on mateiros
  python mock_incendio.py on mateiros --temperatura 45 --umidade 5 --fumaca 98
  python mock_incendio.py on mateiros --lat -10.55 --lon -46.30   # move o marcador no mapa
  python mock_incendio.py off
"""

import argparse
import json
import math
import os

import paho.mqtt.publish as publish
from dotenv import load_dotenv

load_dotenv()

BROKER       = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT         = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_PREFIX = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/monitoramento-br")
TOPIC        = f"{TOPIC_PREFIX}/comando/mock"
FIRMS_RAIO   = float(os.getenv("FIRMS_RAIO_GRAUS", "0.15"))

# Mesma tabela de coordenadas de sensor.py/gateway.py — mantenha em sincronia.
ZONA_COORDS = {
    "anapolis":          (-16.3267, -48.9530),
    "formosa":           (-15.5372, -47.3372),
    "pirinopolis":       (-15.8558, -48.9597),
    "jaragua":           (-15.7529, -49.3344),
    "sandolandia":       (-12.5408, -49.9192),
    "novo-progresso":    ( -7.1261, -55.3853),
    "mirador":           ( -6.3745, -44.3683),
    "mateiros":          (-10.5464, -46.4168),
    "lagoa-da-confusao": (-10.7906, -49.6199),
}
ZONAS = list(ZONA_COORDS.keys())


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


def publicar(payload: dict) -> None:
    publish.single(TOPIC, json.dumps(payload), hostname=BROKER, port=PORT, qos=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Liga/desliga o mock de incêndio do sensor.py em tempo real.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_on = sub.add_parser("on", help="Liga o mock numa zona")
    p_on.add_argument("zona", choices=ZONAS, help="Zona a simular incêndio")
    p_on.add_argument("--temperatura", type=float, default=None, help="°C (default: MOCK_TEMPERATURA do .env)")
    p_on.add_argument("--umidade", type=float, default=None, help="%% (default: MOCK_UMIDADE do .env)")
    p_on.add_argument("--fumaca", type=float, default=None, help="%% (default: MOCK_FUMACA do .env)")
    p_on.add_argument("--lat", type=float, default=None, help="Latitude customizada (default: coordenada real da zona)")
    p_on.add_argument("--lon", type=float, default=None, help="Longitude customizada (default: coordenada real da zona)")

    sub.add_parser("off", help="Desliga o mock — zona volta a dados reais no próximo ciclo")

    args = parser.parse_args()

    if args.comando == "off":
        publicar({"ativo": False})
        print(f"[mock] desligado → {TOPIC}")
        return

    payload = {"ativo": True, "zona": args.zona}
    if args.temperatura is not None:
        payload["temperatura"] = args.temperatura
    if args.umidade is not None:
        payload["umidade"] = args.umidade
    if args.fumaca is not None:
        payload["fumaca"] = args.fumaca
    if (args.lat is None) != (args.lon is None):
        parser.error("--lat e --lon devem ser usados juntos")
    if args.lat is not None:
        lat_real, lon_real = ZONA_COORDS[args.zona]
        raio_km = FIRMS_RAIO * 111
        dist_km = _distancia_km(lat_real, lon_real, args.lat, args.lon)
        if dist_km > raio_km:
            parser.error(
                f"--lat/--lon ficam a {dist_km:.1f} km do centro de {args.zona} "
                f"({lat_real},{lon_real}), fora do raio de busca FIRMS ({raio_km:.1f} km). "
                f"Escolha uma coordenada mais próxima ou omita --lat/--lon."
            )
        payload["lat"] = args.lat
        payload["lon"] = args.lon

    publicar(payload)
    print(f"[mock] ligado → {TOPIC}  {payload}")


if __name__ == "__main__":
    main()
