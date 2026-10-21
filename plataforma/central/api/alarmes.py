"""
alarmes.py — ciclo de vida dos alarmes (RF-PRO-05: ativo → reconhecido →
resolvido, com severidade) e assinaturas de alerta entre inquilinos.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from central import db
from central.api.comum import Banco, Contexto, Pagina, ParametrosPagina, Slug, auditar, conflito_ou_erro, paginar
from central.motor import consulta_alarmes, formatar_alarme

router = APIRouter(prefix="/api/v1", tags=["Alarmes"])

Severidade = Literal["medio", "alto", "critico"]
Estado = Literal["ativo", "reconhecido", "resolvido"]


class Alarme(BaseModel):
    id: str
    inquilino: str
    severidade: Severidade
    estado: Estado
    regra: str | None
    dispositivo: str | None
    site: str | None
    dominio: str | None
    ocorrido_em: datetime
    reconhecido_em: datetime | None
    reconhecido_por: str | None
    resolvido_em: datetime | None
    resolvido_por: str | None
    ocorrencias: int
    origem_alarme_id: str | None
    dados: dict


class Entrega(BaseModel):
    inquilino: str
    estado: Estado
    reconhecido_por: str | None


class Execucao(BaseModel):
    tipo: str
    status: str
    tentativas: int
    ultimo_erro: str | None
    executado_em: datetime | None


class AlarmeDetalhe(Alarme):
    entregas: list[Entrega]
    acoes: list[Execucao]
    comandos: list[dict]


class Transicao(BaseModel):
    comentario: str | None = None


class AssinaturaEntrada(BaseModel):
    nome: str = Field(min_length=1)
    dominio: Slug = Field(description="Domínio de OUTROS inquilinos a acompanhar (ex.: incendio)")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    raio_km: float = Field(gt=0, le=2000)
    severidade_min: Severidade = "alto"


class Assinatura(AssinaturaEntrada):
    id: str
    ativa: bool


def _motor(request: Request):
    return request.app.state.motor


def _do_inquilino(banco, ctx, alarme_id: str) -> dict:
    try:
        ident = uuid.UUID(alarme_id)
    except ValueError as exc:
        raise HTTPException(404, "alarme não encontrado") from exc
    with banco.connect() as conexao:
        linha = (
            conexao.execute(consulta_alarmes().where(db.alarme.c.id == ident, db.alarme.c.inquilino_id == ctx.id))
            .mappings()
            .first()
        )
    if linha is None:
        raise HTTPException(404, "alarme não encontrado")
    return formatar_alarme(dict(linha))


# ── Alarmes ──────────────────────────────────────────────────────────────────


@router.get("/alarmes", response_model=Pagina[Alarme])
def listar(
    banco: Banco,
    ctx: Contexto,
    pag: ParametrosPagina,
    estado: Estado | None = None,
    abertos: bool | None = None,
    severidade: Severidade | None = None,
    dispositivo: str | None = None,
    site: str | None = None,
):
    a = db.alarme
    consulta = consulta_alarmes().where(a.c.inquilino_id == ctx.id)
    if estado:
        consulta = consulta.where(a.c.estado == estado)
    if abertos is not None:
        consulta = consulta.where((a.c.estado != "resolvido") if abertos else (a.c.estado == "resolvido"))
    if severidade:
        consulta = consulta.where(a.c.severidade == severidade)
    if dispositivo:
        consulta = consulta.where(db.dispositivo.c.slug == dispositivo)
    if site:
        consulta = consulta.where(db.site.c.slug == site)
    ordenaveis = {"ocorrido_em": a.c.ocorrido_em, "atualizado_em": a.c.atualizado_em, "severidade": a.c.severidade}
    with banco.connect() as conexao:
        pagina = paginar(conexao, consulta, pag, ordenaveis, "-ocorrido_em")
    pagina["itens"] = [formatar_alarme(x) for x in pagina["itens"]]
    return pagina


@router.get("/alarmes/{alarme_id}", response_model=AlarmeDetalhe)
def obter(banco: Banco, ctx: Contexto, alarme_id: str):
    alarme = _do_inquilino(banco, ctx, alarme_id)
    a, i, e, c, d = db.alarme, db.inquilino, db.execucao_acao, db.comando, db.dispositivo
    ident = uuid.UUID(alarme["id"])
    with banco.connect() as conexao:
        alarme["entregas"] = [
            dict(x)
            for x in conexao.execute(
                select(i.c.slug.label("inquilino"), a.c.estado, a.c.reconhecido_por)
                .join(i, i.c.id == a.c.inquilino_id)
                .where(a.c.origem_alarme_id == ident)
            ).mappings()
        ]
        alarme["acoes"] = [
            dict(x)
            for x in conexao.execute(
                select(e.c.tipo, e.c.status, e.c.tentativas, e.c.ultimo_erro, e.c.executado_em)
                .where(e.c.alarme_id == ident)
                .order_by(e.c.criado_em)
            ).mappings()
        ]
        alarme["comandos"] = [
            {**dict(x), "id": str(x["id"])}
            for x in conexao.execute(
                select(
                    c.c.id,
                    d.c.slug.label("dispositivo"),
                    c.c.acao,
                    c.c.origem,
                    c.c.estado,
                    c.c.enviado_em,
                    c.c.respondido_em,
                    c.c.resposta,
                )
                .join(d, d.c.id == c.c.dispositivo_id)
                .where(c.c.alarme_id == ident, d.c.inquilino_id == ctx.id)
                .order_by(c.c.enviado_em)
            ).mappings()
        ]
    return alarme


@router.post("/alarmes/{alarme_id}/reconhecer", response_model=Alarme)
def reconhecer(request: Request, banco: Banco, ctx: Contexto, alarme_id: str, entrada: Transicao | None = None):
    alarme = _do_inquilino(banco, ctx, alarme_id)
    if alarme["estado"] != "ativo":
        raise HTTPException(409, f"alarme está {alarme['estado']}: só um alarme ativo pode ser reconhecido")
    novo = _motor(request).reconhecer(alarme["id"], ctx.autor)
    with banco.begin() as conexao:
        auditar(
            conexao,
            ctx,
            "alarme",
            uuid.UUID(alarme["id"]),
            "reconhecer",
            antes={"estado": "ativo"},
            depois={"estado": "reconhecido", "comentario": entrada.comentario if entrada else None},
        )
    return novo


@router.post("/alarmes/{alarme_id}/resolver", response_model=Alarme)
def resolver(request: Request, banco: Banco, ctx: Contexto, alarme_id: str, entrada: Transicao | None = None):
    alarme = _do_inquilino(banco, ctx, alarme_id)
    if alarme["estado"] == "resolvido":
        raise HTTPException(409, "alarme já está resolvido")
    novo = _motor(request).resolver(alarme["id"], ctx.autor)
    with banco.begin() as conexao:
        auditar(
            conexao,
            ctx,
            "alarme",
            uuid.UUID(alarme["id"]),
            "resolver",
            antes={"estado": alarme["estado"]},
            depois={"estado": "resolvido", "comentario": entrada.comentario if entrada else None},
        )
    return novo


# ── Assinaturas de alerta ────────────────────────────────────────────────────


def _assinaturas(banco, ctx, ident: uuid.UUID | None = None) -> list[dict]:
    s = db.assinatura_alerta
    consulta = select(
        s.c.id,
        s.c.nome,
        s.c.dominio_slug.label("dominio"),
        s.c.lat,
        s.c.lon,
        s.c.raio_km,
        s.c.severidade_min,
        s.c.ativa,
    ).where(s.c.inquilino_id == ctx.id)
    if ident:
        consulta = consulta.where(s.c.id == ident)
    with banco.connect() as conexao:
        return [{**dict(x), "id": str(x["id"])} for x in conexao.execute(consulta.order_by(s.c.nome)).mappings()]


@router.get("/assinaturas", response_model=Pagina[Assinatura], tags=["Alarmes"])
def listar_assinaturas(banco: Banco, ctx: Contexto, pag: ParametrosPagina):
    itens = _assinaturas(banco, ctx)
    inicio = (pag.pagina - 1) * pag.tamanho
    return {
        "itens": itens[inicio : inicio + pag.tamanho],
        "pagina": pag.pagina,
        "tamanho": pag.tamanho,
        "total": len(itens),
    }


@router.post("/assinaturas", status_code=201, response_model=Assinatura, tags=["Alarmes"])
def criar_assinatura(banco: Banco, ctx: Contexto, entrada: AssinaturaEntrada):
    valores = entrada.model_dump()
    valores["dominio_slug"] = valores.pop("dominio")
    try:
        with banco.begin() as conexao:
            ident = conexao.execute(
                insert(db.assinatura_alerta).values(inquilino_id=ctx.id, **valores).returning(db.assinatura_alerta.c.id)
            ).scalar_one()
            auditar(conexao, ctx, "assinatura_alerta", ident, "criar", depois=entrada.model_dump())
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    return _assinaturas(banco, ctx, ident)[0]


@router.patch("/assinaturas/{assinatura_id}", response_model=Assinatura, tags=["Alarmes"])
def ativar_assinatura(banco: Banco, ctx: Contexto, assinatura_id: uuid.UUID, ativa: bool):
    s = db.assinatura_alerta
    with banco.begin() as conexao:
        alterada = conexao.execute(
            update(s).where(s.c.id == assinatura_id, s.c.inquilino_id == ctx.id).values(ativa=ativa).returning(s.c.id)
        ).scalar()
        if alterada is None:
            raise HTTPException(404, "assinatura não encontrada")
        auditar(conexao, ctx, "assinatura_alerta", assinatura_id, "atualizar", depois={"ativa": ativa})
    return _assinaturas(banco, ctx, assinatura_id)[0]
