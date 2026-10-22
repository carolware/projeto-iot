"""
mensagem.py — modelo da mensagem canônica v1.0 (contratos/mensagem-canonica.v1.schema.json).

Produtores usam `nova_mensagem(...)` para gerar o dicionário pronto para
publicar; consumidores validam com `nucleo.validacao.validar_mensagem` e só
então convertem com `Mensagem.de_dict(...)`.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

VERSAO = "1.0"


@dataclass(frozen=True)
class Medida:
    grandeza: str
    valor: float
    unidade: str

    def para_dict(self) -> dict:
        return {"grandeza": self.grandeza, "valor": self.valor, "unidade": self.unidade}


@dataclass(frozen=True)
class Mensagem:
    id_mensagem: str
    dispositivo: str
    tipo: str
    timestamp: datetime
    versao: str = VERSAO
    via: str | None = None
    fonte: str | None = None
    local: dict | None = None
    medidas: tuple[Medida, ...] = ()
    dados: dict = field(default_factory=dict)

    @classmethod
    def de_dict(cls, bruto: dict) -> "Mensagem":
        """Converte um dicionário JÁ validado contra o schema."""
        return cls(
            id_mensagem=bruto["id_mensagem"],
            dispositivo=bruto["dispositivo"],
            tipo=bruto["tipo"],
            timestamp=parsear_timestamp(bruto["timestamp"]),
            versao=bruto["versao"],
            via=bruto.get("via"),
            fonte=bruto.get("fonte"),
            local=bruto.get("local"),
            medidas=tuple(Medida(m["grandeza"], float(m["valor"]), m["unidade"]) for m in bruto.get("medidas", ())),
            dados=bruto.get("dados", {}),
        )

    def para_dict(self) -> dict:
        saida: dict[str, Any] = {
            "versao": self.versao,
            "id_mensagem": self.id_mensagem,
            "dispositivo": self.dispositivo,
            "tipo": self.tipo,
            "timestamp": formatar_timestamp(self.timestamp),
        }
        if self.via:
            saida["via"] = self.via
        if self.fonte:
            saida["fonte"] = self.fonte
        if self.local:
            saida["local"] = self.local
        if self.medidas:
            saida["medidas"] = [m.para_dict() for m in self.medidas]
        if self.dados:
            saida["dados"] = self.dados
        return saida


def agora_utc() -> datetime:
    return datetime.now(UTC)


def formatar_timestamp(momento: datetime) -> str:
    """ISO 8601 em UTC com sufixo Z (ex.: 2026-10-19T05:10:00.123Z)."""
    if momento.tzinfo is None:
        raise ValueError("timestamp precisa ter fuso horário (use UTC)")
    utc = momento.astimezone(UTC)
    return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parsear_timestamp(texto: str) -> datetime:
    """Lê ISO 8601 com fuso e devolve em UTC; levanta ValueError se não tiver fuso."""
    momento = datetime.fromisoformat(texto)
    if momento.tzinfo is None:
        raise ValueError(f"timestamp sem fuso horário: {texto!r}")
    return momento.astimezone(UTC)


def nova_mensagem(
    dispositivo: str,
    tipo: str,
    *,
    medidas: list[Medida] | None = None,
    dados: dict | None = None,
    timestamp: datetime | None = None,
    via: str | None = None,
    fonte: str | None = None,
    local: dict | None = None,
) -> dict:
    """Cria a mensagem canônica com `id_mensagem` novo, pronta para json.dumps."""
    return Mensagem(
        id_mensagem=str(uuid.uuid4()),
        dispositivo=dispositivo,
        tipo=tipo,
        timestamp=timestamp or agora_utc(),
        via=via,
        fonte=fonte,
        local=local,
        medidas=tuple(medidas or ()),
        dados=dados or {},
    ).para_dict()
