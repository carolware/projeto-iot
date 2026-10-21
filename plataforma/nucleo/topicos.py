"""
topicos.py — convenção de tópicos MQTT (§4.5 / contratos/asyncapi.yaml):

    {inquilino}/{site}/{dominio}/{dispositivo}/{tipo}

tipo ∈ telemetria | evento | estado (dispositivo → plataforma)
       comando                      (plataforma → dispositivo)
"""

import re
from dataclasses import dataclass

TIPOS = ("telemetria", "evento", "estado", "comando")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class Topico:
    inquilino: str
    site: str
    dominio: str
    dispositivo: str
    tipo: str

    def __str__(self) -> str:
        return "/".join((self.inquilino, self.site, self.dominio, self.dispositivo, self.tipo))


def montar_topico(inquilino: str, site: str, dominio: str, dispositivo: str, tipo: str) -> str:
    """Monta o tópico canônico; levanta ValueError se algum nível for inválido."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo inválido: {tipo!r} (esperado um de {TIPOS})")
    for nome, valor in (("inquilino", inquilino), ("site", site), ("dominio", dominio), ("dispositivo", dispositivo)):
        if not _SLUG.match(valor):
            raise ValueError(f"{nome} inválido no tópico: {valor!r}")
    return str(Topico(inquilino, site, dominio, dispositivo, tipo))


def parsear_topico(topico: str) -> Topico | None:
    """Decompõe um tópico canônico; retorna None se não seguir a convenção."""
    partes = topico.split("/")
    if len(partes) != 5:
        return None
    *niveis, tipo = partes
    if tipo not in TIPOS or not all(_SLUG.match(n) for n in niveis):
        return None
    return Topico(*niveis, tipo)


def padrao_assinatura(tipo: str, inquilino: str = "+") -> str:
    """Filtro MQTT para assinar um tipo de mensagem (ex.: '+/+/+/+/telemetria')."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo inválido: {tipo!r}")
    return f"{inquilino}/+/+/+/{tipo}"
