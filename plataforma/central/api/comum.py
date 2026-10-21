"""
comum.py — peças compartilhadas pelas rotas: paginação padronizada (RF-APP-07),
contexto do inquilino, auditoria (RF-DAD-06) e tradução de erros do banco.

O contexto do inquilino (quem é o usuário, sobre qual inquilino age, com quais
papéis) vem do token OIDC — ver seguranca.py.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, Select, func, insert, select
from sqlalchemy.exc import IntegrityError

from central import db

T = TypeVar("T")
SLUG = r"^[a-z0-9][a-z0-9_-]*$"
Slug = Annotated[str, Field(pattern=SLUG, max_length=64)]


# ──────────────────────────────────────────────
# Dependências
# ──────────────────────────────────────────────


def engine(request: Request) -> Engine:
    return request.app.state.engine


Banco = Annotated[Engine, Depends(engine)]


@dataclass(frozen=True)
class ContextoInquilino:
    id: uuid.UUID
    slug: str
    autor: str
    papeis: frozenset[str] = frozenset()

    def pode(self, *papeis: str) -> bool:
        return "admin" in self.papeis or bool(self.papeis & set(papeis))


# ──────────────────────────────────────────────
# Paginação, filtros e ordenação padronizados
# ──────────────────────────────────────────────


class Pagina(BaseModel, Generic[T]):
    itens: list[T]
    pagina: int
    tamanho: int
    total: int


@dataclass(frozen=True)
class Paginacao:
    pagina: int
    tamanho: int
    ordenar: str | None


def paginacao(
    pagina: Annotated[int, Query(ge=1, description="Página, começando em 1")] = 1,
    tamanho: Annotated[int, Query(ge=1, le=500, description="Itens por página")] = 50,
    ordenar: Annotated[
        str | None, Query(pattern=r"^-?[a-z_]+$", description="Campo de ordenação; prefixo '-' = decrescente")
    ] = None,
) -> Paginacao:
    return Paginacao(pagina, tamanho, ordenar)


ParametrosPagina = Annotated[Paginacao, Depends(paginacao)]


def paginar(conexao: Connection, consulta: Select, pag: Paginacao, ordenaveis: dict, padrao: str) -> dict:
    """Aplica ordenação (só campos permitidos), limite e deslocamento; devolve o envelope Pagina."""
    campo = pag.ordenar or padrao
    nome = campo.lstrip("-")
    if nome not in ordenaveis:
        raise HTTPException(422, f"ordenar: campo {nome!r} inválido (use um de {sorted(ordenaveis)})")
    coluna = ordenaveis[nome]
    total = conexao.execute(select(func.count()).select_from(consulta.subquery())).scalar_one()
    linhas = conexao.execute(
        consulta.order_by(coluna.desc() if campo.startswith("-") else coluna.asc())
        .limit(pag.tamanho)
        .offset((pag.pagina - 1) * pag.tamanho)
    ).mappings()
    return {"itens": [dict(linha) for linha in linhas], "pagina": pag.pagina, "tamanho": pag.tamanho, "total": total}


# ──────────────────────────────────────────────
# Auditoria e erros
# ──────────────────────────────────────────────


def auditar(
    conexao: Connection,
    ctx: ContextoInquilino | None,
    entidade: str,
    entidade_id: uuid.UUID | None,
    acao: str,
    antes: dict | None = None,
    depois: dict | None = None,
    autor: str | None = None,
) -> None:
    conexao.execute(
        insert(db.auditoria).values(
            inquilino_id=ctx.id if ctx else None,
            autor=autor or (ctx.autor if ctx else "anonimo"),
            entidade=entidade,
            entidade_id=entidade_id,
            acao=acao,
            antes=jsonable_encoder(antes) if antes is not None else None,
            depois=jsonable_encoder(depois) if depois is not None else None,
        )
    )


def conflito_ou_erro(exc: IntegrityError) -> HTTPException:
    """Traduz violações de integridade em 409 (duplicado) ou 422 (referência inválida)."""
    texto = str(exc.orig)
    if "duplicate key" in texto or "unique" in texto.lower():
        detalhe = re.search(r"Key \((.+?)\)=\((.+?)\)", texto)
        return HTTPException(409, f"já existe: {detalhe.group(1)}={detalhe.group(2)}" if detalhe else "já existe")
    return HTTPException(422, "referência ou valor inválido")
