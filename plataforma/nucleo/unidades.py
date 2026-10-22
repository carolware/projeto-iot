"""
unidades.py — normalização de unidades UCUM (RF-PRO-01).

Cada perfil de dispositivo declara a unidade canônica de cada grandeza; uma
medida que chega em outra unidade conhecida é convertida antes de gravar.
"""

from collections.abc import Callable

_CONVERSOES: dict[tuple[str, str], Callable[[float], float]] = {
    ("K", "Cel"): lambda v: v - 273.15,
    ("Cel", "K"): lambda v: v + 273.15,
    ("[degF]", "Cel"): lambda v: (v - 32) * 5 / 9,
    ("Cel", "[degF]"): lambda v: v * 9 / 5 + 32,
    ("1", "%"): lambda v: v * 100,
    ("%", "1"): lambda v: v / 100,
    ("mg/m3", "ug/m3"): lambda v: v * 1000,
    ("ug/m3", "mg/m3"): lambda v: v / 1000,
    ("km/h", "m/s"): lambda v: v / 3.6,
    ("m/s", "km/h"): lambda v: v * 3.6,
}


def converter(valor: float, de: str, para: str) -> float | None:
    """Converte `valor` da unidade `de` para `para`; None se não houver conversão conhecida."""
    if de == para:
        return valor
    funcao = _CONVERSOES.get((de, para))
    return funcao(valor) if funcao else None
