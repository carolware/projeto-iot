"""
dispositivos.py — cadastro (RF-DIS-01), online/offline pelo intervalo do
perfil (RF-DIS-06), último estado (RF-DAD-02) e histórico de telemetria por
grandeza e intervalo, bruto ou agregado por hora (RF-DAD-01, RF-PRO-04).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Interval, insert, literal_column, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from central import db
from central.api.comum import (
    Banco,
    Contexto,
    Pagina,
    ParametrosPagina,
    Slug,
    auditar,
    conflito_ou_erro,
    paginar,
)

router = APIRouter(prefix="/api/v1/dispositivos", tags=["Dispositivos"])

# Um dispositivo fica offline depois de TOLERANCIA × intervalo esperado sem dados:
# um sensor rural de 1 h não aparece offline após 5 min de silêncio.
TOLERANCIA_OFFLINE = 3

Tipo = Literal["estacao", "terminal", "atuador", "conector"]


class DispositivoEntrada(BaseModel):
    slug: Slug
    nome: str = Field(min_length=1)
    tipo: Tipo
    dominio: Slug
    perfil: Slug
    site: Slug
    area: Slug | None = None
    fabricante: str | None = None
    firmware: str | None = None


class DispositivoAtualizacao(BaseModel):
    nome: str | None = Field(default=None, min_length=1)
    site: Slug | None = None
    area: Slug | None = None
    fabricante: str | None = None
    firmware: str | None = None


class Dispositivo(DispositivoEntrada):
    online: bool
    revogado: bool
    ultimo_contato_em: datetime | None
    intervalo_esperado_s: int


class LeituraAtual(BaseModel):
    grandeza: str
    valor: float
    unidade: str
    suspeita: bool
    tempo: datetime


class EstadoDispositivo(BaseModel):
    dispositivo: str
    online: bool
    ultimo_contato_em: datetime | None
    leituras: list[LeituraAtual]
    relatado: dict | None = None
    relatado_em: datetime | None = None


class PontoTelemetria(BaseModel):
    tempo: datetime
    grandeza: str
    valor: float | None = None
    unidade: str | None = None
    suspeita: bool | None = None
    minimo: float | None = None
    maximo: float | None = None
    amostras: int | None = None


# ──────────────────────────────────────────────
# Consultas
# ──────────────────────────────────────────────


def _expr_online():
    d, p = db.dispositivo, db.perfil_dispositivo
    janela = (p.c.intervalo_esperado_s * TOLERANCIA_OFFLINE) * literal_column("interval '1 second'", Interval)
    return func.coalesce(d.c.ultimo_contato_em >= func.now() - janela, False)


def _consulta_dispositivos(ctx):
    d, p, dom, s, a = db.dispositivo, db.perfil_dispositivo, db.dominio, db.site, db.area
    return (
        select(
            d.c.slug,
            d.c.nome,
            d.c.tipo,
            dom.c.slug.label("dominio"),
            p.c.slug.label("perfil"),
            s.c.slug.label("site"),
            a.c.slug.label("area"),
            d.c.fabricante,
            d.c.firmware,
            _expr_online().label("online"),
            d.c.revogado_em.is_not(None).label("revogado"),
            d.c.ultimo_contato_em,
            p.c.intervalo_esperado_s,
        )
        .join(p, p.c.id == d.c.perfil_id)
        .join(dom, dom.c.id == p.c.dominio_id)
        .join(s, s.c.id == d.c.site_id)
        .outerjoin(a, a.c.id == d.c.area_id)
        .where(d.c.inquilino_id == ctx.id)
    )


def _obter(conexao, ctx, slug: str) -> dict:
    linha = conexao.execute(_consulta_dispositivos(ctx).where(db.dispositivo.c.slug == slug)).mappings().first()
    if linha is None:
        raise HTTPException(404, f"dispositivo {slug!r} não encontrado")
    return dict(linha)


def _resolver_local(conexao, ctx, site: str, area: str | None):
    s, a = db.site, db.area
    site_id = conexao.execute(select(s.c.id).where(s.c.inquilino_id == ctx.id, s.c.slug == site)).scalar()
    if site_id is None:
        raise HTTPException(422, f"site {site!r} não existe")
    area_id = None
    if area:
        area_id = conexao.execute(select(a.c.id).where(a.c.site_id == site_id, a.c.slug == area)).scalar()
        if area_id is None:
            raise HTTPException(422, f"área {area!r} não existe no site {site!r}")
    return site_id, area_id


def _id_dispositivo(conexao, ctx, slug: str):
    d = db.dispositivo
    dispositivo_id = conexao.execute(select(d.c.id).where(d.c.inquilino_id == ctx.id, d.c.slug == slug)).scalar()
    if dispositivo_id is None:
        raise HTTPException(404, f"dispositivo {slug!r} não encontrado")
    return dispositivo_id


# ──────────────────────────────────────────────
# Rotas
# ──────────────────────────────────────────────


@router.get("", response_model=Pagina[Dispositivo])
def listar(
    banco: Banco,
    ctx: Contexto,
    pag: ParametrosPagina,
    site: str | None = None,
    dominio: str | None = None,
    tipo: Tipo | None = None,
    online: bool | None = None,
):
    d, dom, s = db.dispositivo, db.dominio, db.site
    consulta = _consulta_dispositivos(ctx)
    if site:
        consulta = consulta.where(s.c.slug == site)
    if dominio:
        consulta = consulta.where(dom.c.slug == dominio)
    if tipo:
        consulta = consulta.where(d.c.tipo == tipo)
    if online is not None:
        consulta = consulta.where(_expr_online() == online)
    ordenaveis = {"slug": d.c.slug, "nome": d.c.nome, "ultimo_contato_em": d.c.ultimo_contato_em}
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, ordenaveis, "slug")


@router.post("", status_code=201, response_model=Dispositivo)
def criar(banco: Banco, ctx: Contexto, entrada: DispositivoEntrada):
    p, dom = db.perfil_dispositivo, db.dominio
    try:
        with banco.begin() as conexao:
            perfil_id = conexao.execute(
                select(p.c.id)
                .join(dom, dom.c.id == p.c.dominio_id)
                .where(dom.c.inquilino_id == ctx.id, dom.c.slug == entrada.dominio, p.c.slug == entrada.perfil)
            ).scalar()
            if perfil_id is None:
                raise HTTPException(422, f"perfil {entrada.dominio}/{entrada.perfil} não existe no catálogo")
            site_id, area_id = _resolver_local(conexao, ctx, entrada.site, entrada.area)
            novo_id = conexao.execute(
                insert(db.dispositivo)
                .values(
                    inquilino_id=ctx.id,
                    perfil_id=perfil_id,
                    site_id=site_id,
                    area_id=area_id,
                    **entrada.model_dump(include={"slug", "nome", "tipo", "fabricante", "firmware"}),
                )
                .returning(db.dispositivo.c.id)
            ).scalar_one()
            auditar(conexao, ctx, "dispositivo", novo_id, "criar", depois=entrada.model_dump())
            return _obter(conexao, ctx, entrada.slug)
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc


@router.get("/{slug}", response_model=Dispositivo)
def obter(banco: Banco, ctx: Contexto, slug: str):
    with banco.connect() as conexao:
        return _obter(conexao, ctx, slug)


@router.patch("/{slug}", response_model=Dispositivo)
def atualizar(banco: Banco, ctx: Contexto, slug: str, entrada: DispositivoAtualizacao):
    mudancas = entrada.model_dump(exclude_unset=True)
    with banco.begin() as conexao:
        antes = _obter(conexao, ctx, slug)
        valores = {k: v for k, v in mudancas.items() if k in {"nome", "fabricante", "firmware"}}
        if "site" in mudancas or "area" in mudancas:
            site_id, area_id = _resolver_local(
                conexao, ctx, mudancas.get("site") or antes["site"], mudancas.get("area")
            )
            valores |= {"site_id": site_id, "area_id": area_id}
        if valores:
            d = db.dispositivo
            conexao.execute(update(d).where(d.c.inquilino_id == ctx.id, d.c.slug == slug).values(**valores))
        depois = _obter(conexao, ctx, slug)
        campos = set(DispositivoAtualizacao.model_fields)
        auditar(
            conexao,
            ctx,
            "dispositivo",
            _id_dispositivo(conexao, ctx, slug),
            "atualizar",
            antes={k: antes[k] for k in campos},
            depois={k: depois[k] for k in campos},
        )
        return depois


@router.get("/{slug}/estado", response_model=EstadoDispositivo, summary="Último estado (sem consultar o histórico)")
def estado(banco: Banco, ctx: Contexto, slug: str):
    ue, g = db.ultimo_estado, db.gemeo
    with banco.connect() as conexao:
        dispositivo = _obter(conexao, ctx, slug)
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        leituras = conexao.execute(
            select(ue.c.grandeza, ue.c.valor, ue.c.unidade, ue.c.suspeita, ue.c.tempo)
            .where(ue.c.dispositivo_id == dispositivo_id)
            .order_by(ue.c.grandeza)
        ).mappings()
        gemeo = conexao.execute(
            select(g.c.relatado, g.c.relatado_em).where(g.c.dispositivo_id == dispositivo_id)
        ).first()
        return {
            "dispositivo": slug,
            "online": dispositivo["online"],
            "ultimo_contato_em": dispositivo["ultimo_contato_em"],
            "leituras": [dict(linha) for linha in leituras],
            "relatado": gemeo.relatado if gemeo else None,
            "relatado_em": gemeo.relatado_em if gemeo else None,
        }


@router.get("/{slug}/telemetria", response_model=Pagina[PontoTelemetria], summary="Histórico por grandeza e intervalo")
def telemetria(
    banco: Banco,
    ctx: Contexto,
    slug: str,
    pag: ParametrosPagina,
    grandeza: str | None = None,
    inicio: Annotated[datetime | None, Query(description="ISO 8601 com fuso; inclusivo")] = None,
    fim: Annotated[datetime | None, Query(description="ISO 8601 com fuso; exclusivo")] = None,
    agregacao: Annotated[Literal["bruto", "1h"], Query(description="1h = média/mín/máx por hora")] = "bruto",
):
    if inicio and fim and inicio >= fim:
        raise HTTPException(422, "inicio deve ser anterior a fim")
    with banco.connect() as conexao:
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        if agregacao == "bruto":
            t = db.telemetria
            tempo = t.c.tempo
            consulta = select(t.c.tempo, t.c.grandeza, t.c.valor, t.c.unidade, t.c.suspeita)
        else:
            t = db.telemetria_1h
            tempo = t.c.hora
            consulta = select(
                t.c.hora.label("tempo"),
                t.c.grandeza,
                t.c.media.label("valor"),
                t.c.minimo,
                t.c.maximo,
                t.c.amostras,
            )
        consulta = consulta.where(t.c.dispositivo_id == dispositivo_id)
        if grandeza:
            consulta = consulta.where(t.c.grandeza == grandeza)
        if inicio:
            consulta = consulta.where(tempo >= inicio)
        if fim:
            consulta = consulta.where(tempo < fim)
        return paginar(conexao, consulta, pag, {"tempo": tempo, "grandeza": t.c.grandeza}, "-tempo")
