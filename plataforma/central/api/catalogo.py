"""
catalogo.py — inquilinos, locais (site → área) e catálogo configurável de
domínios e perfis (RF-CEN-01..03). Criar um domínio ou perfil novo é só
configuração: nenhuma linha de código muda (RNF-08, padrão Type Object).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from central import db
from central.api.comum import (
    Banco,
    Pagina,
    ParametrosPagina,
    Slug,
    auditar,
    conflito_ou_erro,
    paginar,
)
from central.api.seguranca import Admin, Gestao, Integracao, Leitura

router = APIRouter(prefix="/api/v1", tags=["Catálogo e locais"])


# ──────────────────────────────────────────────
# Esquemas
# ──────────────────────────────────────────────


class InquilinoEntrada(BaseModel):
    slug: Slug
    nome: str = Field(min_length=1)
    cenario: Literal["urbano", "rural"]


class Inquilino(InquilinoEntrada):
    pass


class LocalEntrada(BaseModel):
    slug: Slug
    nome: str = Field(min_length=1)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    raio_km: float | None = Field(default=None, gt=0)


class Site(LocalEntrada):
    pass


class Area(LocalEntrada):
    site: str


class DominioEntrada(BaseModel):
    slug: Slug
    nome: str = Field(min_length=1)
    descricao: str | None = None


class Dominio(DominioEntrada):
    pass


class GrandezaPerfil(BaseModel):
    grandeza: Slug
    unidade: str = Field(min_length=1, max_length=32, description="Código UCUM (ex.: Cel, %, ug/m3, 1)")
    valor_min: float | None = None
    valor_max: float | None = None

    @model_validator(mode="after")
    def _faixa(self):
        if self.valor_min is not None and self.valor_max is not None and self.valor_min > self.valor_max:
            raise ValueError("valor_min maior que valor_max")
        return self


class PerfilEntrada(BaseModel):
    dominio: Slug
    slug: Slug
    nome: str = Field(min_length=1)
    intervalo_esperado_s: int = Field(gt=0, description="Intervalo esperado entre envios; base do online/offline")
    grandezas: list[GrandezaPerfil] = []


class Perfil(PerfilEntrada):
    pass


# ──────────────────────────────────────────────
# Inquilinos (administração da plataforma)
# ──────────────────────────────────────────────


@router.get("/inquilinos", response_model=Pagina[Inquilino], tags=["Administração"])
def listar_inquilinos(
    banco: Banco, admin: Admin, pag: ParametrosPagina, cenario: Literal["urbano", "rural"] | None = None
):
    i = db.inquilino
    consulta = select(i.c.slug, i.c.nome, i.c.cenario)
    if cenario:
        consulta = consulta.where(i.c.cenario == cenario)
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"slug": i.c.slug, "nome": i.c.nome}, "slug")


@router.post("/inquilinos", status_code=201, response_model=Inquilino, tags=["Administração"])
def criar_inquilino(banco: Banco, admin: Admin, entrada: InquilinoEntrada):
    try:
        with banco.begin() as conexao:
            novo_id = conexao.execute(insert(db.inquilino).values(**entrada.model_dump()).returning(db.inquilino.c.id))
            novo_id = novo_id.scalar_one()
            auditar(conexao, None, "inquilino", novo_id, "criar", depois=entrada.model_dump(), autor=admin.nome)
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    return entrada


# ──────────────────────────────────────────────
# Locais: site → área
# ──────────────────────────────────────────────


def _site_id(conexao, ctx, slug: str):
    s = db.site
    site_id = conexao.execute(select(s.c.id).where(s.c.inquilino_id == ctx.id, s.c.slug == slug)).scalar()
    if site_id is None:
        raise HTTPException(404, f"site {slug!r} não encontrado")
    return site_id


@router.get("/sites", response_model=Pagina[Site])
def listar_sites(banco: Banco, ctx: Leitura, pag: ParametrosPagina):
    s = db.site
    consulta = select(s.c.slug, s.c.nome, s.c.lat, s.c.lon, s.c.raio_km).where(s.c.inquilino_id == ctx.id)
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"slug": s.c.slug, "nome": s.c.nome}, "slug")


@router.post("/sites", status_code=201, response_model=Site)
def criar_site(banco: Banco, ctx: Gestao, entrada: LocalEntrada):
    try:
        with banco.begin() as conexao:
            novo_id = conexao.execute(
                insert(db.site).values(inquilino_id=ctx.id, **entrada.model_dump()).returning(db.site.c.id)
            ).scalar_one()
            auditar(conexao, ctx, "site", novo_id, "criar", depois=entrada.model_dump())
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    return entrada


@router.get("/sites/{site}", response_model=Site)
def obter_site(banco: Banco, ctx: Leitura, site: str):
    s = db.site
    with banco.connect() as conexao:
        linha = (
            conexao.execute(
                select(s.c.slug, s.c.nome, s.c.lat, s.c.lon, s.c.raio_km).where(
                    s.c.inquilino_id == ctx.id, s.c.slug == site
                )
            )
            .mappings()
            .first()
        )
    if linha is None:
        raise HTTPException(404, f"site {site!r} não encontrado")
    return dict(linha)


@router.get("/sites/{site}/areas", response_model=Pagina[Area])
def listar_areas(banco: Banco, ctx: Leitura, site: str, pag: ParametrosPagina):
    a = db.area
    with banco.connect() as conexao:
        site_id = _site_id(conexao, ctx, site)
        consulta = select(a.c.slug, a.c.nome, a.c.lat, a.c.lon, a.c.raio_km).where(a.c.site_id == site_id)
        pagina = paginar(conexao, consulta, pag, {"slug": a.c.slug, "nome": a.c.nome}, "slug")
    for item in pagina["itens"]:
        item["site"] = site
    return pagina


@router.post("/sites/{site}/areas", status_code=201, response_model=Area)
def criar_area(banco: Banco, ctx: Gestao, site: str, entrada: LocalEntrada):
    try:
        with banco.begin() as conexao:
            site_id = _site_id(conexao, ctx, site)
            novo_id = conexao.execute(
                insert(db.area).values(site_id=site_id, **entrada.model_dump()).returning(db.area.c.id)
            ).scalar_one()
            auditar(conexao, ctx, "area", novo_id, "criar", depois={"site": site, **entrada.model_dump()})
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    return {"site": site, **entrada.model_dump()}


# ──────────────────────────────────────────────
# Catálogo: domínios e perfis
# ──────────────────────────────────────────────


@router.get("/catalogo/dominios", response_model=Pagina[Dominio])
def listar_dominios(banco: Banco, ctx: Leitura, pag: ParametrosPagina):
    d = db.dominio
    consulta = select(d.c.slug, d.c.nome, d.c.descricao).where(d.c.inquilino_id == ctx.id)
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"slug": d.c.slug, "nome": d.c.nome}, "slug")


@router.post("/catalogo/dominios", status_code=201, response_model=Dominio)
def criar_dominio(banco: Banco, ctx: Gestao, entrada: DominioEntrada):
    try:
        with banco.begin() as conexao:
            novo_id = conexao.execute(
                insert(db.dominio).values(inquilino_id=ctx.id, **entrada.model_dump()).returning(db.dominio.c.id)
            ).scalar_one()
            auditar(conexao, ctx, "dominio", novo_id, "criar", depois=entrada.model_dump())
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    return entrada


def _perfis(conexao, ctx, dominio: str | None = None, slug: str | None = None) -> list[dict]:
    p, d, g = db.perfil_dispositivo, db.dominio, db.perfil_grandeza
    consulta = (
        select(p.c.id, d.c.slug.label("dominio"), p.c.slug, p.c.nome, p.c.intervalo_esperado_s)
        .join(d, d.c.id == p.c.dominio_id)
        .where(d.c.inquilino_id == ctx.id)
        .order_by(d.c.slug, p.c.slug)
    )
    if dominio:
        consulta = consulta.where(d.c.slug == dominio)
    if slug:
        consulta = consulta.where(p.c.slug == slug)
    perfis = [dict(linha) for linha in conexao.execute(consulta).mappings()]
    if perfis:
        grandezas = conexao.execute(
            select(g).where(g.c.perfil_id.in_([pf["id"] for pf in perfis])).order_by(g.c.grandeza)
        ).mappings()
        por_perfil: dict = {}
        for linha in grandezas:
            por_perfil.setdefault(linha["perfil_id"], []).append(
                {k: linha[k] for k in ("grandeza", "unidade", "valor_min", "valor_max")}
            )
        for pf in perfis:
            pf["grandezas"] = por_perfil.get(pf.pop("id"), [])
    return perfis


@router.get("/catalogo/perfis", response_model=Pagina[Perfil])
def listar_perfis(banco: Banco, ctx: Leitura, pag: ParametrosPagina, dominio: Annotated[str | None, Query()] = None):
    with banco.connect() as conexao:
        perfis = _perfis(conexao, ctx, dominio)
    inicio = (pag.pagina - 1) * pag.tamanho
    return {
        "itens": perfis[inicio : inicio + pag.tamanho],
        "pagina": pag.pagina,
        "tamanho": pag.tamanho,
        "total": len(perfis),
    }


@router.post("/catalogo/perfis", status_code=201, response_model=Perfil)
def criar_perfil(banco: Banco, ctx: Integracao, entrada: PerfilEntrada):
    d = db.dominio
    try:
        with banco.begin() as conexao:
            dominio_id = conexao.execute(
                select(d.c.id).where(d.c.inquilino_id == ctx.id, d.c.slug == entrada.dominio)
            ).scalar()
            if dominio_id is None:
                raise HTTPException(422, f"domínio {entrada.dominio!r} não existe no catálogo do inquilino")
            perfil_id = conexao.execute(
                insert(db.perfil_dispositivo)
                .values(
                    dominio_id=dominio_id,
                    slug=entrada.slug,
                    nome=entrada.nome,
                    intervalo_esperado_s=entrada.intervalo_esperado_s,
                )
                .returning(db.perfil_dispositivo.c.id)
            ).scalar_one()
            if entrada.grandezas:
                conexao.execute(
                    insert(db.perfil_grandeza),
                    [{"perfil_id": perfil_id, **g.model_dump()} for g in entrada.grandezas],
                )
            auditar(conexao, ctx, "perfil_dispositivo", perfil_id, "criar", depois=entrada.model_dump())
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    return entrada
