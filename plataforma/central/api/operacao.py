"""
operacao.py — consulta da fila de mensagens inválidas (RF-ING-06), trilha de
auditoria (RF-DAD-06) e situação dos nós de borda (RF-EDG-06).
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from central import db
from central.api.comum import Banco, Pagina, ParametrosPagina, paginar
from central.api.seguranca import Admin, Borda, Gestao, Leitura

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
    segundos_sem_sincronizar: float | None


class AcessoBorda(BaseModel):
    senhas: str = Field(description="password_file do Mosquitto (só hashes)")
    acl: str


class EstadoBorda(BaseModel):
    fila_pendente: int = Field(ge=0)
    versao: str | None = None


@router.get("/dlq", response_model=Pagina[MensagemInvalida], tags=["Administração"])
def listar_dlq(banco: Banco, admin: Admin, pag: ParametrosPagina, origem: str | None = None):
    q = db.dlq
    consulta = select(q.c.id, q.c.recebido_em, q.c.origem, q.c.motivo, q.c.payload)
    if origem:
        consulta = consulta.where(q.c.origem.startswith(origem))
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"recebido_em": q.c.recebido_em, "id": q.c.id}, "-id")


@router.get("/auditoria", response_model=Pagina[RegistroAuditoria])
def listar_auditoria(banco: Banco, ctx: Gestao, pag: ParametrosPagina, entidade: str | None = None):
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
def listar_bordas(banco: Banco, ctx: Leitura, pag: ParametrosPagina):
    b = db.no_borda
    consulta = select(
        b.c.slug,
        b.c.nome,
        b.c.versao,
        b.c.fila_pendente,
        b.c.ultimo_sync_em,
        func.extract("epoch", func.now() - b.c.ultimo_sync_em).label("segundos_sem_sincronizar"),
    ).where(b.c.inquilino_id == ctx.id)
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"slug": b.c.slug, "ultimo_sync_em": b.c.ultimo_sync_em}, "slug")


@router.put("/bordas/{slug}/estado", status_code=204, summary="Nó de borda informa fila pendente e versão")
def informar_estado_borda(banco: Banco, borda: Borda, slug: str, estado: EstadoBorda):
    b = db.no_borda
    with banco.begin() as conexao:
        alterados = conexao.execute(
            update(b).where(b.c.slug == slug).values(ultimo_sync_em=func.now(), **estado.model_dump(exclude_none=True))
        ).rowcount
    if not alterados:
        raise HTTPException(404, f"nó de borda {slug!r} não cadastrado")


@router.get("/bordas/{slug}/acesso", response_model=AcessoBorda, summary="Credenciais e ACL do broker local da borda")
def acesso_da_borda(request: Request, borda: Borda, slug: str):
    arquivos = request.app.state.acesso.da_borda(slug)
    if arquivos is None:
        raise HTTPException(404, f"nó de borda {slug!r} sem credencial")
    return {"senhas": arquivos[0], "acl": arquivos[1]}
