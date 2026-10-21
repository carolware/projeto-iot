"""
mock_incendio.py — CLI para ligar/desligar o mock de incêndio em tempo real.

Publica no tópico de controle da simulação (simulacao/mock) do broker por
onde a zona fala com a plataforma — o do nó de borda da região, ou o da
central para zonas sem borda — e também no da central, onde o conector FIRMS
escuta para fabricar o foco de satélite. Não precisa reiniciar nada.

Uso:
  python plataforma/simuladores/mock_incendio.py on mateiros
  python plataforma/simuladores/mock_incendio.py on mateiros --temperatura 45 --umidade 5 --fumaca 98
  python plataforma/simuladores/mock_incendio.py on mateiros --lat -10.55 --lon -46.30   # move o foco no mapa
  python plataforma/simuladores/mock_incendio.py off
"""

import argparse
import json
import os
import sys
from pathlib import Path

import paho.mqtt.publish as publish
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # permite rodar como script

from nucleo.geo import distancia_km  # noqa: E402
from simuladores.zonas import BROKER_NA_MAQUINA, TOPICO_MOCK, ZONAS  # noqa: E402

load_dotenv()

FIRMS_RAIO = float(os.getenv("FIRMS_RAIO_GRAUS", "0.15"))


def publicar(payload: dict, regioes: set) -> None:
    for regiao in regioes:
        host, porta = BROKER_NA_MAQUINA[regiao]
        publish.single(TOPICO_MOCK, json.dumps(payload), hostname=host, port=porta, qos=1)
        print(f"[mock] → {host}:{porta} {TOPICO_MOCK}  {payload}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Liga/desliga o mock de incêndio das estações em tempo real.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_on = sub.add_parser("on", help="Liga o mock numa zona")
    p_on.add_argument("zona", choices=list(ZONAS), help="Zona a simular incêndio")
    p_on.add_argument("--temperatura", type=float, default=None, help="°C (default: MOCK_TEMPERATURA do .env)")
    p_on.add_argument("--umidade", type=float, default=None, help="%% (default: MOCK_UMIDADE do .env)")
    p_on.add_argument("--fumaca", type=float, default=None, help="%% (default: MOCK_FUMACA do .env)")
    p_on.add_argument("--lat", type=float, default=None, help="Latitude customizada (default: coordenada real da zona)")
    p_on.add_argument("--lon", type=float, default=None, help="Longitude customizada (default: coordenada real da zona)")

    sub.add_parser("off", help="Desliga o mock — zona volta a dados reais no próximo ciclo")

    args = parser.parse_args()

    if args.comando == "off":
        publicar({"ativo": False}, set(BROKER_NA_MAQUINA))
        return

    zona = ZONAS[args.zona]
    payload = {"ativo": True, "zona": zona.slug}
    for campo in ("temperatura", "umidade", "fumaca"):
        if getattr(args, campo) is not None:
            payload[campo] = getattr(args, campo)
    if (args.lat is None) != (args.lon is None):
        parser.error("--lat e --lon devem ser usados juntos")
    if args.lat is not None:
        raio_km = FIRMS_RAIO * 111
        dist_km = distancia_km(zona.lat, zona.lon, args.lat, args.lon)
        if dist_km > raio_km:
            parser.error(
                f"--lat/--lon ficam a {dist_km:.1f} km do centro de {zona.slug} "
                f"({zona.lat},{zona.lon}), fora do raio de busca FIRMS ({raio_km:.1f} km). "
                f"Escolha uma coordenada mais próxima ou omita --lat/--lon."
            )
        payload["lat"] = args.lat
        payload["lon"] = args.lon

    # broker da região (estações) + central (conector FIRMS)
    publicar(payload, {zona.regiao, None})


if __name__ == "__main__":
    main()
