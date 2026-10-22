"""
ingestao.py — pipeline de ingestão (Pipes and Filters, Figura 5.1):

    receber → autenticar → validar → deduplicar → ordenar → enriquecer → publicar
    mensagens rejeitadas → fila de mensagens inválidas (DLQ)

É o mesmo caso de uso na central (Postgres) e no nó de borda (SQLite): só
mudam os adaptadores que implementam as portas abaixo (arquitetura hexagonal).
Autenticação por credencial do dispositivo entra na etapa A4; aqui o
"autenticar" confere cadastro, revogação e coerência com o tópico.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from nucleo.mensagem import Mensagem, agora_utc, formatar_timestamp, parsear_timestamp
from nucleo.topicos import Topico
from nucleo.unidades import converter
from nucleo.validacao import ErroValidacao, validar_mensagem

log = logging.getLogger("nucleo.ingestao")

# ──────────────────────────────────────────────
# Modelos do domínio
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class GrandezaPerfil:
    grandeza: str
    unidade: str
    valor_min: float | None = None
    valor_max: float | None = None

    def fora_da_faixa(self, valor: float) -> bool:
        return (self.valor_min is not None and valor < self.valor_min) or (
            self.valor_max is not None and valor > self.valor_max
        )


@dataclass(frozen=True)
class DispositivoCadastrado:
    """O que a ingestão precisa saber do cadastro para autenticar e enriquecer."""

    id: str
    slug: str
    inquilino: str
    site: str
    dominio: str
    perfil: str
    area: str | None = None
    grandezas: dict[str, GrandezaPerfil] = field(default_factory=dict)
    revogado: bool = False
    geo: tuple[float, float] | None = None  # (lat, lon) do centro da área, ou do site, no cadastro


@dataclass(frozen=True)
class MedidaEnriquecida:
    grandeza: str
    valor: float
    unidade: str
    suspeita: bool = False


@dataclass(frozen=True)
class MensagemEnriquecida:
    mensagem: Mensagem
    dispositivo: DispositivoCadastrado
    medidas: tuple[MedidaEnriquecida, ...]
    recebido_em: datetime

    @property
    def id_mensagem(self) -> str:
        return self.mensagem.id_mensagem

    @property
    def area(self) -> str | None:
        local = self.mensagem.local or {}
        return local.get("area") or self.dispositivo.area

    @property
    def posicao(self) -> tuple[float, float] | None:
        """(lat, lon): a da própria mensagem (local.geo, GeoJSON lon/lat) ou a do cadastro."""
        geo = (self.mensagem.local or {}).get("geo")
        if geo:
            lon, lat = geo["coordinates"]
            return lat, lon
        return self.dispositivo.geo

    def para_dict(self) -> dict:
        """Mensagem canônica + contexto do cadastro, como circula no barramento interno."""
        saida = self.mensagem.para_dict()
        if self.medidas:
            saida["medidas"] = [
                {"grandeza": m.grandeza, "valor": m.valor, "unidade": m.unidade, "suspeita": m.suspeita}
                for m in self.medidas
            ]
        saida["contexto"] = {
            "dispositivo_id": self.dispositivo.id,
            "inquilino": self.dispositivo.inquilino,
            "site": self.dispositivo.site,
            "area": self.dispositivo.area,
            "dominio": self.dispositivo.dominio,
            "perfil": self.dispositivo.perfil,
            "geo": list(self.dispositivo.geo) if self.dispositivo.geo else None,
        }
        saida["recebido_em"] = formatar_timestamp(self.recebido_em)
        return saida

    @classmethod
    def de_dict(cls, saida: dict) -> "MensagemEnriquecida":
        """Inverso de `para_dict`: reconstrói a mensagem que outra instância publicou no barramento."""
        ctx = saida["contexto"]
        dispositivo = DispositivoCadastrado(
            id=ctx["dispositivo_id"],
            slug=saida["dispositivo"],
            inquilino=ctx["inquilino"],
            site=ctx["site"],
            dominio=ctx["dominio"],
            perfil=ctx["perfil"],
            area=ctx.get("area"),
            geo=tuple(ctx["geo"]) if ctx.get("geo") else None,
        )
        medidas = tuple(
            MedidaEnriquecida(m["grandeza"], float(m["valor"]), m["unidade"], bool(m.get("suspeita")))
            for m in saida.get("medidas", ())
        )
        return cls(Mensagem.de_dict(saida), dispositivo, medidas, parsear_timestamp(saida["recebido_em"]))


@dataclass(frozen=True)
class Recusa:
    origem: str
    payload: object
    erros: tuple[ErroValidacao, ...]


@dataclass(frozen=True)
class Entrada:
    """Uma mensagem bruta como chegou: de onde (DLQ/logs) e por qual tópico (coerência com o cadastro)."""

    bruto: object
    origem: str
    topico: Topico | None = None


Status = Literal["aceita", "duplicada", "recusada"]


@dataclass(frozen=True)
class Resultado:
    indice: int
    status: Status
    id_mensagem: str | None = None
    erros: tuple[ErroValidacao, ...] = ()


# ──────────────────────────────────────────────
# Portas (implementadas por adaptadores da central e da borda)
# ──────────────────────────────────────────────


class Catalogo(Protocol):
    def buscar_dispositivos(self, slugs: set[str]) -> dict[str, DispositivoCadastrado]: ...


class Repositorio(Protocol):
    def gravar(self, mensagens: list[MensagemEnriquecida]) -> set[str]:
        """Grava de forma idempotente e devolve os `id_mensagem` que eram novos."""
        ...


class FilaInvalidas(Protocol):
    def registrar(self, recusas: list[Recusa]) -> None: ...


class Barramento(Protocol):
    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None: ...

    def publicar_recusada(self, recusa: Recusa) -> None: ...


class BarramentoNulo:
    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None:
        pass

    def publicar_recusada(self, recusa: Recusa) -> None:
        pass


class BarramentoComposto:
    """Entrega cada mensagem a vários assinantes (ex.: MQTT + motor de regras) — Observer."""

    def __init__(self, *assinantes: Barramento):
        self.assinantes = assinantes

    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None:
        for assinante in self.assinantes:
            assinante.publicar_aceita(mensagem)

    def publicar_recusada(self, recusa: Recusa) -> None:
        for assinante in self.assinantes:
            assinante.publicar_recusada(recusa)


# ──────────────────────────────────────────────
# Caso de uso
# ──────────────────────────────────────────────


class _Recusada(Exception):
    def __init__(self, *erros: ErroValidacao):
        self.erros = erros


class PipelineIngestao:
    def __init__(
        self,
        catalogo: Catalogo,
        repositorio: Repositorio,
        fila_invalidas: FilaInvalidas,
        barramento: Barramento | None = None,
        relogio: Callable[[], datetime] = agora_utc,
    ):
        self.catalogo = catalogo
        self.repositorio = repositorio
        self.fila_invalidas = fila_invalidas
        self.barramento = barramento or BarramentoNulo()
        self.relogio = relogio

    def processar(
        self,
        brutos: list[object],
        origem: str,
        topico: Topico | None = None,
        autorizado: Callable[[DispositivoCadastrado], bool] | None = None,
    ) -> list[Resultado]:
        """
        Processa uma mensagem (lista de 1) ou um lote; devolve um resultado por
        mensagem, na ordem recebida. `autorizado` diz se a credencial de quem
        enviou pode falar por aquele dispositivo (HTTP); no MQTT, o broker já
        garante isso pela ACL de tópicos.
        """
        return self.processar_entradas([Entrada(bruto, origem, topico) for bruto in brutos], autorizado)

    def processar_entradas(
        self,
        entradas: list["Entrada"],
        autorizado: Callable[[DispositivoCadastrado], bool] | None = None,
    ) -> list[Resultado]:
        """Como `processar`, mas cada mensagem com sua origem e tópico (micro-lote do MQTT)."""
        brutos = [e.bruto for e in entradas]
        recebido_em = self.relogio()
        resultados: dict[int, Resultado] = {}
        recusas: list[Recusa] = []
        candidatas: list[tuple[int, Mensagem]] = []

        # receber → validar
        for indice, entrada in enumerate(entradas):
            erros = validar_mensagem(entrada.bruto, entrada.topico)
            if erros:
                resultados[indice] = Resultado(indice, "recusada", _id_de(entrada.bruto), tuple(erros))
                recusas.append(Recusa(entrada.origem, entrada.bruto, tuple(erros)))
            else:
                candidatas.append((indice, Mensagem.de_dict(entrada.bruto)))

        # autenticar → enriquecer
        cadastro = self.catalogo.buscar_dispositivos({m.dispositivo for _, m in candidatas})
        enriquecidas: list[tuple[int, MensagemEnriquecida]] = []
        for indice, mensagem in candidatas:
            try:
                dispositivo = _autenticar(
                    mensagem, cadastro.get(mensagem.dispositivo), entradas[indice].topico, autorizado
                )
                medidas = _enriquecer_medidas(mensagem, dispositivo)
            except _Recusada as recusada:
                resultados[indice] = Resultado(indice, "recusada", mensagem.id_mensagem, recusada.erros)
                recusas.append(Recusa(entradas[indice].origem, brutos[indice], recusada.erros))
                continue
            enriquecidas.append((indice, MensagemEnriquecida(mensagem, dispositivo, medidas, recebido_em)))

        # deduplicar dentro do próprio lote (o repositório deduplica contra o histórico)
        vistas: set[str] = set()
        unicas: list[tuple[int, MensagemEnriquecida]] = []
        for indice, enriquecida in enriquecidas:
            if enriquecida.id_mensagem in vistas:
                resultados[indice] = Resultado(indice, "duplicada", enriquecida.id_mensagem)
            else:
                vistas.add(enriquecida.id_mensagem)
                unicas.append((indice, enriquecida))

        # ordenar pelo horário da medição, não pela chegada
        unicas.sort(key=lambda par: par[1].mensagem.timestamp)

        novas = self.repositorio.gravar([e for _, e in unicas]) if unicas else set()
        for indice, enriquecida in unicas:
            if enriquecida.id_mensagem in novas:
                resultados[indice] = Resultado(indice, "aceita", enriquecida.id_mensagem)
                self.barramento.publicar_aceita(enriquecida)
            else:
                resultados[indice] = Resultado(indice, "duplicada", enriquecida.id_mensagem)

        if recusas:
            self.fila_invalidas.registrar(recusas)
            for recusa in recusas:
                self.barramento.publicar_recusada(recusa)

        saida = [resultados[i] for i in range(len(brutos))]
        _registrar(saida, entradas)
        return saida


# ──────────────────────────────────────────────
# Filtros
# ──────────────────────────────────────────────


def _registrar(resultados: list[Resultado], entradas: list[Entrada]) -> None:
    """Log de correlação: cada mensagem com seu id_mensagem (aceitas em DEBUG, recusadas em WARNING)."""
    detalhar = log.isEnabledFor(logging.DEBUG)
    for r in resultados:
        if r.status == "recusada":
            log.warning(
                "mensagem recusada: %s",
                "; ".join(str(e) for e in r.erros),
                extra={"id_mensagem": r.id_mensagem, "origem": entradas[r.indice].origem},
            )
        elif detalhar:
            bruto = entradas[r.indice].bruto if isinstance(entradas[r.indice].bruto, dict) else {}
            log.debug(
                "mensagem %s",
                r.status,
                extra={
                    "id_mensagem": r.id_mensagem,
                    "dispositivo": bruto.get("dispositivo"),
                    "via": bruto.get("via"),
                    "origem": entradas[r.indice].origem,
                },
            )


def _id_de(bruto: object) -> str | None:
    if isinstance(bruto, dict) and isinstance(bruto.get("id_mensagem"), str):
        return bruto["id_mensagem"]
    return None


def _autenticar(
    mensagem: Mensagem,
    dispositivo: DispositivoCadastrado | None,
    topico: Topico | None,
    autorizado: Callable[[DispositivoCadastrado], bool] | None,
) -> DispositivoCadastrado:
    if dispositivo is None:
        raise _Recusada(ErroValidacao("dispositivo", f"{mensagem.dispositivo!r} não está cadastrado"))
    if autorizado is not None and not autorizado(dispositivo):
        raise _Recusada(ErroValidacao("dispositivo", "a credencial usada não pode enviar dados deste dispositivo"))
    if dispositivo.revogado:
        raise _Recusada(ErroValidacao("dispositivo", "credencial do dispositivo revogada"))
    if mensagem.tipo == "comando":
        raise _Recusada(ErroValidacao("tipo", "comando é enviado pela plataforma, não recebido"))
    if topico is not None:
        esperado = (dispositivo.inquilino, dispositivo.site, dispositivo.dominio)
        if (topico.inquilino, topico.site, topico.dominio) != esperado:
            raise _Recusada(
                ErroValidacao(
                    "topico",
                    "inquilino/site/dominio do tópico diferem do cadastro (esperado {}/{}/{})".format(*esperado),
                )
            )
    return dispositivo


def _enriquecer_medidas(mensagem: Mensagem, dispositivo: DispositivoCadastrado) -> tuple[MedidaEnriquecida, ...]:
    medidas: list[MedidaEnriquecida] = []
    erros: list[ErroValidacao] = []
    for i, medida in enumerate(mensagem.medidas):
        perfil = dispositivo.grandezas.get(medida.grandeza)
        if perfil is None:
            erros.append(
                ErroValidacao(
                    f"medidas/{i}/grandeza", f"{medida.grandeza!r} não pertence ao perfil {dispositivo.perfil!r}"
                )
            )
            continue
        valor = converter(medida.valor, medida.unidade, perfil.unidade)
        if valor is None:
            erros.append(
                ErroValidacao(f"medidas/{i}/unidade", f"sem conversão de {medida.unidade!r} para {perfil.unidade!r}")
            )
            continue
        medidas.append(MedidaEnriquecida(medida.grandeza, valor, perfil.unidade, perfil.fora_da_faixa(valor)))
    if erros:
        raise _Recusada(*erros)
    return tuple(medidas)
