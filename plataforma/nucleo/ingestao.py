"""
ingestao.py — pipeline de ingestão (Pipes and Filters, Figura 5.1):

    receber → autenticar → validar → deduplicar → ordenar → enriquecer → publicar
    mensagens rejeitadas → fila de mensagens inválidas (DLQ)

É o mesmo caso de uso na central (Postgres) e no nó de borda (SQLite): só
mudam os adaptadores que implementam as portas abaixo (arquitetura hexagonal).
Autenticação por credencial do dispositivo entra na etapa A4; aqui o
"autenticar" confere cadastro, revogação e coerência com o tópico.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from nucleo.mensagem import Mensagem, agora_utc, formatar_timestamp
from nucleo.topicos import Topico
from nucleo.unidades import converter
from nucleo.validacao import ErroValidacao, validar_mensagem

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

    def para_dict(self) -> dict:
        """Mensagem canônica + contexto do cadastro, como circula no barramento interno."""
        saida = self.mensagem.para_dict()
        if self.medidas:
            saida["medidas"] = [
                {"grandeza": m.grandeza, "valor": m.valor, "unidade": m.unidade, "suspeita": m.suspeita}
                for m in self.medidas
            ]
        saida["contexto"] = {
            "inquilino": self.dispositivo.inquilino,
            "site": self.dispositivo.site,
            "area": self.dispositivo.area,
            "dominio": self.dispositivo.dominio,
            "perfil": self.dispositivo.perfil,
        }
        saida["recebido_em"] = formatar_timestamp(self.recebido_em)
        return saida


@dataclass(frozen=True)
class Recusa:
    origem: str
    payload: object
    erros: tuple[ErroValidacao, ...]


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

    def processar(self, brutos: list[object], origem: str, topico: Topico | None = None) -> list[Resultado]:
        """Processa uma mensagem (lista de 1) ou um lote; devolve um resultado por mensagem, na ordem recebida."""
        recebido_em = self.relogio()
        resultados: dict[int, Resultado] = {}
        recusas: list[Recusa] = []
        candidatas: list[tuple[int, Mensagem]] = []

        # receber → validar
        for indice, bruto in enumerate(brutos):
            erros = validar_mensagem(bruto, topico)
            if erros:
                resultados[indice] = Resultado(indice, "recusada", _id_de(bruto), tuple(erros))
                recusas.append(Recusa(origem, bruto, tuple(erros)))
            else:
                candidatas.append((indice, Mensagem.de_dict(bruto)))

        # autenticar → enriquecer
        cadastro = self.catalogo.buscar_dispositivos({m.dispositivo for _, m in candidatas})
        enriquecidas: list[tuple[int, MensagemEnriquecida]] = []
        for indice, mensagem in candidatas:
            try:
                dispositivo = _autenticar(mensagem, cadastro.get(mensagem.dispositivo), topico)
                medidas = _enriquecer_medidas(mensagem, dispositivo)
            except _Recusada as recusada:
                resultados[indice] = Resultado(indice, "recusada", mensagem.id_mensagem, recusada.erros)
                recusas.append(Recusa(origem, brutos[indice], recusada.erros))
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

        return [resultados[i] for i in range(len(brutos))]


# ──────────────────────────────────────────────
# Filtros
# ──────────────────────────────────────────────


def _id_de(bruto: object) -> str | None:
    if isinstance(bruto, dict) and isinstance(bruto.get("id_mensagem"), str):
        return bruto["id_mensagem"]
    return None


def _autenticar(
    mensagem: Mensagem, dispositivo: DispositivoCadastrado | None, topico: Topico | None
) -> DispositivoCadastrado:
    if dispositivo is None:
        raise _Recusada(ErroValidacao("dispositivo", f"{mensagem.dispositivo!r} não está cadastrado"))
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
