"""
operacao.py — consulta da fila de mensagens inválidas (RF-ING-06), trilha de
auditoria (RF-DAD-06) e situação dos nós de borda (RF-EDG-06).
"""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from central import db
from central.api.comum import Banco, Contexto, Pagina, ParametrosPagina, paginar

router = APIRouter(prefix="/api/v1", tags=["Operação"])


class MensagemInvalida(BaseModel):
    id: int
    recebido_em: datetime
    origem: str
    motivo: str
    payload: str


class RegistroAuditoria(BaseModel):
    quando: datetime
    autor: str
    entidade: str
    entidade_id: str | None
    acao: str
    antes: dict | None
    depois: dict | None


class NoBorda(BaseModel):
    slug: str
    nome: str
    versao: str | None
    fila_pendente: int
    ultimo_sync_em: datetime | None


@router.get("/dlq", response_model=Pagina[MensagemInvalida], tags=["Administração"])
def listar_dlq(banco: Banco, pag: ParametrosPagina, origem: str | None = None):
    q = db.dlq
    consulta = select(q.c.id, q.c.recebido_em, q.c.origem, q.c.motivo, q.c.payload)
    if origem:
        consulta = consulta.where(q.c.origem.startswith(origem))
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"recebido_em": q.c.recebido_em, "id": q.c.id}, "-id")


@router.get("/auditoria", response_model=Pagina[RegistroAuditoria])
def listar_auditoria(banco: Banco, ctx: Contexto, pag: ParametrosPagina, entidade: str | None = None):
    a = db.auditoria
    consulta = select(a.c.quando, a.c.autor, a.c.entidade, a.c.entidade_id, a.c.acao, a.c.antes, a.c.depois).where(
        a.c.inquilino_id == ctx.id
    )
    if entidade:
        consulta = consulta.where(a.c.entidade == entidade)
    with banco.connect() as conexao:
        pagina = paginar(conexao, consulta, pag, {"quando": a.c.quando}, "-quando")
    for item in pagina["itens"]:
        item["entidade_id"] = str(item["entidade_id"]) if item["entidade_id"] else None
    return pagina


@router.get("/bordas", response_model=Pagina[NoBorda])
def listar_bordas(banco: Banco, ctx: Contexto, pag: ParametrosPagina):
    b = db.no_borda
    consulta = select(b.c.slug, b.c.nome, b.c.versao, b.c.fila_pendente, b.c.ultimo_sync_em).where(
        b.c.inquilino_id == ctx.id
    )
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"slug": b.c.slug, "ultimo_sync_em": b.c.ultimo_sync_em}, "slug")
