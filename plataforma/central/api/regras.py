"""
regras.py — regras de processamento configuráveis (RF-PRO-02/03): criar, ver,
ativar/desativar e alterar sem tocar em código. Regras de escopo "borda" são
distribuídas aos nós de borda (RF-EDG-04): uma alteração aqui passa a valer
no nó na próxima conexão.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from central import db
from central.acoes import mascarar
from central.api.comum import Banco, Pagina, ParametrosPagina, Slug, auditar, conflito_ou_erro
from central.api.seguranca import Borda, Gestao, Leitura
from central.motor import carregar_regras
from nucleo.regras import validar_regra

router = APIRouter(prefix="/api/v1", tags=["Regras"])

Severidade = Literal["medio", "alto", "critico"]


class RegraEntrada(BaseModel):
    dominio: Slug
    nome: Slug
    severidade: Severidade
    escopo: Literal["central", "borda"] = "central"
    janela_s: int = Field(default=0, ge=0, description="> 0: a condição precisa valer durante toda a janela")
    condicao: dict = Field(
        examples=[{"todas": [{"grandeza": "temperatura", "op": ">", "valor": 30}]}],
        description="{'todas': [...]}, {'alguma': [...]} ou {'ausencia': {'segundos': N, 'perfil': slug?}}; "
        "com 'todas'/'alguma', opcional 'area': slug de área ou {'lat', 'lon', 'raio_km'} (área geográfica)",
    )
    acoes: list[dict] = Field(
        default_factory=lambda: [{"tipo": "alarme"}],
        examples=[[{"tipo": "alarme"}, {"tipo": "notificar", "canal": "sms", "destino": "+5562999990000"}]],
    )


class RegraAtualizacao(BaseModel):
    severidade: Severidade | None = None
    janela_s: int | None = Field(default=None, ge=0)
    condicao: dict | None = None
    acoes: list[dict] | None = None
    ativa: bool | None = None


class RegraSaida(RegraEntrada):
    id: str
    ativa: bool


def _motor(request: Request):
    return request.app.state.motor


def _listar(
    banco, ctx, dominio: str | None = None, nome: str | None = None, incluir_inativas: bool = True
) -> list[dict]:
    r, ra, dom = db.regra, db.regra_acao, db.dominio
    consulta = (
        select(
            r.c.id,
            dom.c.slug.label("dominio"),
            r.c.nome,
            r.c.severidade,
            r.c.escopo,
            r.c.janela_s,
            r.c.condicao,
            r.c.ativa,
        )
        .join(dom, dom.c.id == r.c.dominio_id)
        .where(r.c.inquilino_id == ctx.id)
        .order_by(dom.c.slug, r.c.nome)
    )
    if dominio:
        consulta = consulta.where(dom.c.slug == dominio)
    if nome:
        consulta = consulta.where(r.c.nome == nome)
    if not incluir_inativas:
        consulta = consulta.where(r.c.ativa)
    with banco.connect() as conexao:
        regras = [dict(x) for x in conexao.execute(consulta).mappings()]
        if regras:
            acoes: dict = {}
            for ac in conexao.execute(
                select(ra).where(ra.c.regra_id.in_([x["id"] for x in regras])).order_by(ra.c.ordem)
            ).mappings():
                acoes.setdefault(ac["regra_id"], []).append({"tipo": ac["tipo"], **ac["config"]})
            for x in regras:
                x["acoes"] = acoes.get(x["id"], [])
                x["id"] = str(x["id"])
    return regras


def _gravar_acoes(conexao, regra_id: uuid.UUID, acoes: list[dict]) -> None:
    conexao.execute(delete(db.regra_acao).where(db.regra_acao.c.regra_id == regra_id))
    if acoes:
        conexao.execute(
            insert(db.regra_acao),
            [
                {
                    "regra_id": regra_id,
                    "tipo": a["tipo"],
                    "config": {k: v for k, v in a.items() if k != "tipo"},
                    "ordem": i,
                }
                for i, a in enumerate(acoes)
            ],
        )


def _mascarar_destinos(regras: list[dict], ctx) -> list[dict]:
    """Telefones e e-mails de notificação só aparecem completos para o gestor (LGPD, RF-DAD-05)."""
    if ctx.pode("gestor"):
        return regras
    for regra in regras:
        regra["acoes"] = [a | {"destino": mascarar(str(a["destino"]))} if "destino" in a else a for a in regra["acoes"]]
    return regras


@router.get("/regras", response_model=Pagina[RegraSaida])
def listar(banco: Banco, ctx: Leitura, pag: ParametrosPagina, dominio: str | None = None):
    regras = _mascarar_destinos(_listar(banco, ctx, dominio), ctx)
    inicio = (pag.pagina - 1) * pag.tamanho
    return {
        "itens": regras[inicio : inicio + pag.tamanho],
        "pagina": pag.pagina,
        "tamanho": pag.tamanho,
        "total": len(regras),
    }


@router.get("/regras/{nome}", response_model=RegraSaida)
def obter(banco: Banco, ctx: Leitura, nome: str):
    regras = _listar(banco, ctx, nome=nome)
    if not regras:
        raise HTTPException(404, f"regra {nome!r} não encontrada")
    return _mascarar_destinos(regras, ctx)[0]


@router.post("/regras", status_code=201, response_model=RegraSaida)
def criar(request: Request, banco: Banco, ctx: Gestao, entrada: RegraEntrada):
    erros = validar_regra(entrada.model_dump())
    if erros:
        raise HTTPException(422, erros)
    d = db.dominio
    try:
        with banco.begin() as conexao:
            dominio_id = conexao.execute(
                select(d.c.id).where(d.c.inquilino_id == ctx.id, d.c.slug == entrada.dominio)
            ).scalar()
            if dominio_id is None:
                raise HTTPException(422, f"domínio {entrada.dominio!r} não existe no catálogo do inquilino")
            regra_id = conexao.execute(
                insert(db.regra)
                .values(
                    inquilino_id=ctx.id,
                    dominio_id=dominio_id,
                    **entrada.model_dump(include={"nome", "severidade", "escopo", "janela_s", "condicao"}),
                )
                .returning(db.regra.c.id)
            ).scalar_one()
            _gravar_acoes(conexao, regra_id, entrada.acoes)
            auditar(conexao, ctx, "regra", regra_id, "criar", depois=entrada.model_dump())
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    _motor(request).invalidar_regras()
    return obter(banco, ctx, entrada.nome)


@router.patch("/regras/{nome}", response_model=RegraSaida)
def atualizar(request: Request, banco: Banco, ctx: Gestao, nome: str, entrada: RegraAtualizacao):
    antes = obter(banco, ctx, nome)
    mudancas = entrada.model_dump(exclude_unset=True)
    candidata = {**antes, **mudancas}
    erros = validar_regra(candidata)
    if erros:
        raise HTTPException(422, erros)
    r = db.regra
    regra_id = uuid.UUID(antes["id"])
    with banco.begin() as conexao:
        valores = {k: v for k, v in mudancas.items() if k != "acoes"}
        if valores:
            conexao.execute(update(r).where(r.c.id == regra_id).values(**valores))
        if "acoes" in mudancas:
            _gravar_acoes(conexao, regra_id, mudancas["acoes"])
        auditar(conexao, ctx, "regra", regra_id, "atualizar", antes=antes, depois=candidata)
    _motor(request).invalidar_regras()
    return obter(banco, ctx, nome)


@router.get(
    "/bordas/{slug}/regras",
    tags=["Operação"],
    summary="Regras de escopo borda para um nó de borda (sincronizadas a cada conexão)",
)
def regras_da_borda(banco: Banco, borda: Borda, slug: str, dominio: Annotated[str | None, Query()] = None):
    inquilino = borda.inquilino
    return [
        {k: v for k, v in r.para_dict().items() if k != "id"}
        | {"acoes": [a for a in r.acoes if a["tipo"] == "comando"]}
        for r in carregar_regras(banco, inquilino, dominio, escopo="borda")
    ]
