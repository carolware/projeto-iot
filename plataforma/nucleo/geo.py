"""
geo.py — cálculos geográficos usados por regras, assinaturas de alerta e
pelo conector FIRMS. Coordenadas sempre em graus decimais.
"""

import math

RAIO_TERRA_KM = 6371.0


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos pela fórmula de Haversine."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return RAIO_TERRA_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def dentro_do_raio(centro_lat: float, centro_lon: float, raio_km: float, lat: float, lon: float) -> bool:
    """True se (lat, lon) está a no máximo `raio_km` do centro."""
    return distancia_km(centro_lat, centro_lon, lat, lon) <= raio_km
