"""
dispositivos.py — cadastro (RF-DIS-01), online/offline pelo intervalo do
perfil (RF-DIS-06), último estado (RF-DAD-02), histórico de telemetria por
grandeza e intervalo, bruto ou agregado por hora (RF-DAD-01, RF-PRO-04),
comandos com resposta (RF-DIS-05), gêmeo do dispositivo (RF-DIS-04),
tags/grupos para filtrar a frota (RF-DIS-07; grupo = tag `grupo:xxx`) e
cadastro em lote por CSV (RF-DIS-03).
"""

import csv
import io
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AfterValidator, BaseModel, Field, ValidationError
from sqlalchemy import Interval, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from central import db
from central.acoes import FalhaAcao, enviar_comando
from central.api.comum import (
    Banco,
    Pagina,
    ParametrosPagina,
    Slug,
    auditar,
    conflito_ou_erro,
    paginar,
)
from central.api.seguranca import Integracao, Leitura, Operacao
from nucleo.credenciais import gerar_credencial, hash_senha

router = APIRouter(prefix="/api/v1/dispositivos", tags=["Dispositivos"])

# Um dispositivo fica offline depois de TOLERANCIA × intervalo esperado sem dados:
# um sensor rural de 1 h não aparece offline após 5 min de silêncio.
TOLERANCIA_OFFLINE = 3

Tipo = Literal["estacao", "terminal", "atuador", "conector"]

# Tag é um slug que pode levar um prefixo "chave:" — "grupo:serra-geral", "bioma:cerrado".
Tag = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*(:[a-z0-9][a-z0-9_-]*)?$", max_length=64)]
Tags = Annotated[list[Tag], Field(max_length=32), AfterValidator(lambda tags: sorted(set(tags)))]


class DispositivoBase(BaseModel):
    slug: Slug
    nome: str = Field(min_length=1)
    tipo: Tipo
    dominio: Slug
    perfil: Slug
    site: Slug
    area: Slug | None = None
    fabricante: str | None = None
    firmware: str | None = None
    tags: Tags = []


class DispositivoEntrada(DispositivoBase):
    credencial: str | None = Field(
        default=None,
        min_length=16,
        description="Credencial já gravada no aparelho (provisionamento). Vazio = a plataforma gera uma.",
    )


class DispositivoAtualizacao(BaseModel):
    nome: str | None = Field(default=None, min_length=1)
    site: Slug | None = None
    area: Slug | None = None
    fabricante: str | None = None
    firmware: str | None = None
    tags: Tags | None = Field(default=None, description="Substitui o conjunto de tags inteiro")


class Dispositivo(DispositivoBase):
    online: bool
    revogado: bool
    ultimo_contato_em: datetime | None
    intervalo_esperado_s: int


class DispositivoCriado(Dispositivo):
    credencial: str = Field(description="Mostrada só agora: a plataforma guarda apenas o hash")


class ReprovisionarEntrada(BaseModel):
    credencial: str | None = Field(default=None, min_length=16, description="Credencial já gravada no aparelho")


class NovaCredencial(BaseModel):
    dispositivo: str
    credencial: str


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


class LinhaLote(BaseModel):
    linha: int = Field(description="Número da linha no CSV (o cabeçalho é a linha 1)")
    slug: str | None
    criado: bool
    erro: str | None = None
    credencial: str | None = Field(default=None, description="Mostrada só agora, como no cadastro unitário")


class ResultadoLote(BaseModel):
    total: int
    criados: int
    falhas: int
    linhas: list[LinhaLote]


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
            d.c.tags,
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


def _inserir(conexao, ctx, entrada: DispositivoEntrada) -> str:
    """Grava o dispositivo e a auditoria; devolve a credencial em claro (só existe neste momento)."""
    p, dom = db.perfil_dispositivo, db.dominio
    credencial = entrada.credencial or gerar_credencial()
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
            credencial_hash=hash_senha(credencial),
            **entrada.model_dump(include={"slug", "nome", "tipo", "fabricante", "firmware", "tags"}),
        )
        .returning(db.dispositivo.c.id)
    ).scalar_one()
    auditar(conexao, ctx, "dispositivo", novo_id, "criar", depois=entrada.model_dump(exclude={"credencial"}))
    return credencial


@router.get("", response_model=Pagina[Dispositivo])
def listar(
    banco: Banco,
    ctx: Leitura,
    pag: ParametrosPagina,
    site: str | None = None,
    dominio: str | None = None,
    tipo: Tipo | None = None,
    online: bool | None = None,
    tag: Annotated[list[str] | None, Query(description="Repetível; o dispositivo precisa ter todas")] = None,
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
    if tag:
        consulta = consulta.where(d.c.tags.contains(tag))
    ordenaveis = {"slug": d.c.slug, "nome": d.c.nome, "ultimo_contato_em": d.c.ultimo_contato_em}
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, ordenaveis, "slug")


@router.post("", status_code=201, response_model=DispositivoCriado)
def criar(request: Request, banco: Banco, ctx: Integracao, entrada: DispositivoEntrada):
    try:
        with banco.begin() as conexao:
            credencial = _inserir(conexao, ctx, entrada)
            criado = _obter(conexao, ctx, entrada.slug)
    except IntegrityError as exc:
        raise conflito_ou_erro(exc) from exc
    request.app.state.acesso.atualizar()
    return criado | {"credencial": credencial}


MAX_LINHAS_LOTE = 5000
COLUNAS_LOTE = list(DispositivoEntrada.model_fields)


async def _corpo_csv(request: Request) -> str:
    try:
        return (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "o CSV precisa estar em UTF-8") from exc


def _linha_csv(linha: dict) -> dict:
    """Célula vazia = campo ausente; `tags` vem separada por ';' ("grupo:jalapao;bioma:cerrado")."""
    valores = {k.strip(): (v or "").strip() for k, v in linha.items() if k}
    valores = {k: v for k, v in valores.items() if v}
    if "tags" in valores:
        valores["tags"] = [t.strip() for t in valores["tags"].split(";") if t.strip()]
    return valores


def _mensagem(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'linha'}: {e['msg']}" for e in exc.errors())


@router.post(
    "/lote",
    response_model=ResultadoLote,
    summary="Cadastrar em lote a partir de um CSV",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string"}, "example": ",".join(COLUNAS_LOTE)}},
        }
    },
)
def criar_lote(request: Request, banco: Banco, ctx: Integracao, corpo: Annotated[str, Depends(_corpo_csv)]):
    """
    Corpo `text/csv` com cabeçalho; colunas iguais às do cadastro unitário
    (só `slug`, `nome`, `tipo`, `dominio`, `perfil` e `site` são obrigatórias).

    Cada linha é independente: as válidas são gravadas e as inválidas voltam no
    relatório com o motivo, sem desfazer as demais. As credenciais geradas
    aparecem **uma única vez**, nesta resposta. A ACL do broker é regerada uma
    vez ao fim do lote, não por linha.
    """
    leitor = csv.DictReader(io.StringIO(corpo))
    faltando = {"slug", "nome", "tipo", "dominio", "perfil", "site"} - set(leitor.fieldnames or [])
    if faltando:
        raise HTTPException(400, f"cabeçalho sem as colunas obrigatórias: {', '.join(sorted(faltando))}")
    desconhecidas = set(leitor.fieldnames) - set(COLUNAS_LOTE)
    if desconhecidas:
        raise HTTPException(400, f"colunas desconhecidas: {', '.join(sorted(desconhecidas))}")
    linhas = list(leitor)
    if len(linhas) > MAX_LINHAS_LOTE:
        raise HTTPException(413, f"no máximo {MAX_LINHAS_LOTE} linhas por lote")

    resultado = []
    with banco.begin() as conexao:
        for numero, linha in enumerate(linhas, start=2):
            valores = _linha_csv(linha)
            item = {"linha": numero, "slug": valores.get("slug"), "criado": False}
            try:
                entrada = DispositivoEntrada.model_validate(valores)
                with conexao.begin_nested():  # savepoint: a falha da linha não desfaz as outras
                    item |= {"criado": True, "credencial": _inserir(conexao, ctx, entrada)}
            except ValidationError as exc:
                item["erro"] = _mensagem(exc)
            except HTTPException as exc:
                item["erro"] = exc.detail
            except IntegrityError as exc:
                item["erro"] = conflito_ou_erro(exc).detail
            resultado.append(item)
    criados = sum(item["criado"] for item in resultado)
    if criados:
        request.app.state.acesso.atualizar()
    return {"total": len(resultado), "criados": criados, "falhas": len(resultado) - criados, "linhas": resultado}


@router.post(
    "/{slug}/credencial", response_model=NovaCredencial, summary="Emitir nova credencial (a anterior deixa de valer)"
)
def renovar_credencial(
    request: Request, banco: Banco, ctx: Integracao, slug: str, entrada: ReprovisionarEntrada | None = None
):
    """Sem corpo: a plataforma gera uma credencial nova. Com `credencial`: reprovisiona com a do aparelho."""
    credencial = (entrada.credencial if entrada else None) or gerar_credencial()
    d = db.dispositivo
    with banco.begin() as conexao:
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        conexao.execute(
            update(d).where(d.c.id == dispositivo_id).values(credencial_hash=hash_senha(credencial), revogado_em=None)
        )
        auditar(conexao, ctx, "dispositivo", dispositivo_id, "renovar_credencial")
    request.app.state.acesso.atualizar()
    return {"dispositivo": slug, "credencial": credencial}


@router.post("/{slug}/revogar", response_model=Dispositivo, summary="Revogar a credencial do dispositivo")
def revogar(request: Request, banco: Banco, ctx: Integracao, slug: str):
    """A conexão seguinte é recusada na central e, após a próxima sincronização, nos nós de borda (RF-DIS-02)."""
    d = db.dispositivo
    with banco.begin() as conexao:
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        conexao.execute(
            update(d).where(d.c.id == dispositivo_id, d.c.revogado_em.is_(None)).values(revogado_em=func.now())
        )
        auditar(conexao, ctx, "dispositivo", dispositivo_id, "revogar")
        revogado = _obter(conexao, ctx, slug)
    request.app.state.acesso.atualizar()
    return revogado


@router.get("/{slug}", response_model=Dispositivo)
def obter(banco: Banco, ctx: Leitura, slug: str):
    with banco.connect() as conexao:
        return _obter(conexao, ctx, slug)


@router.patch("/{slug}", response_model=Dispositivo)
def atualizar(banco: Banco, ctx: Integracao, slug: str, entrada: DispositivoAtualizacao):
    mudancas = entrada.model_dump(exclude_unset=True)
    with banco.begin() as conexao:
        antes = _obter(conexao, ctx, slug)
        valores = {k: v for k, v in mudancas.items() if k in {"nome", "fabricante", "firmware"}}
        if mudancas.get("tags") is not None:
            valores["tags"] = mudancas["tags"]
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
def estado(banco: Banco, ctx: Leitura, slug: str):
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
    ctx: Leitura,
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


# ──────────────────────────────────────────────
# Comandos (RF-DIS-05) e gêmeo (RF-DIS-04)
# ──────────────────────────────────────────────


class ComandoEntrada(BaseModel):
    acao: Slug
    dados: dict = Field(default_factory=dict, description="Parâmetros adicionais do comando")


class Comando(BaseModel):
    id: str
    acao: str
    origem: str
    estado: Literal["pendente", "enviado", "confirmado", "falhou"]
    enviado_em: datetime | None
    respondido_em: datetime | None
    resposta: dict | None
    alarme_id: str | None


class Gemeo(BaseModel):
    dispositivo: str
    desejado: dict
    desejado_em: datetime | None
    relatado: dict
    relatado_em: datetime | None
    diferenca: dict = Field(description="Chaves do desejado ainda não relatadas pelo dispositivo")


@router.post("/{slug}/comandos", status_code=202, response_model=Comando)
def enviar(request: Request, banco: Banco, ctx: Operacao, slug: str, entrada: ComandoEntrada):
    with banco.connect() as conexao:
        _id_dispositivo(conexao, ctx, slug)  # 404 antes de qualquer efeito
    try:
        comando_id = enviar_comando(
            banco, request.app.state.publicar, ctx.slug, slug, {**entrada.dados, "acao": entrada.acao}, origem="painel"
        )
    except FalhaAcao as exc:
        raise HTTPException(409, str(exc)) from exc
    with banco.begin() as conexao:
        auditar(
            conexao,
            ctx,
            "comando",
            uuid.UUID(comando_id),
            "enviar",
            depois={"dispositivo": slug, **entrada.model_dump()},
        )
    return _comandos(banco, ctx, slug, comando_id)[0]


@router.get("/{slug}/comandos", response_model=Pagina[Comando])
def listar_comandos(banco: Banco, ctx: Leitura, slug: str, pag: ParametrosPagina):
    c = db.comando
    with banco.connect() as conexao:
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        consulta = select(
            c.c.id, c.c.acao, c.c.origem, c.c.estado, c.c.enviado_em, c.c.respondido_em, c.c.resposta, c.c.alarme_id
        ).where(c.c.dispositivo_id == dispositivo_id)
        pagina = paginar(conexao, consulta, pag, {"enviado_em": c.c.enviado_em}, "-enviado_em")
    pagina["itens"] = [_formatar_comando(x) for x in pagina["itens"]]
    return pagina


def _comandos(banco, ctx, slug: str, comando_id: str) -> list[dict]:
    c = db.comando
    with banco.connect() as conexao:
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        linhas = conexao.execute(
            select(
                c.c.id, c.c.acao, c.c.origem, c.c.estado, c.c.enviado_em, c.c.respondido_em, c.c.resposta, c.c.alarme_id
            ).where(c.c.dispositivo_id == dispositivo_id, c.c.id == uuid.UUID(comando_id))
        ).mappings()
        return [_formatar_comando(dict(x)) for x in linhas]


def _formatar_comando(linha: dict) -> dict:
    return linha | {"id": str(linha["id"]), "alarme_id": str(linha["alarme_id"]) if linha["alarme_id"] else None}


def _gemeo(conexao, ctx, slug: str) -> dict:
    g = db.gemeo
    dispositivo_id = _id_dispositivo(conexao, ctx, slug)
    linha = conexao.execute(select(g).where(g.c.dispositivo_id == dispositivo_id)).mappings().first()
    linha = dict(linha) if linha else {"desejado": {}, "desejado_em": None, "relatado": {}, "relatado_em": None}
    diferenca = {k: v for k, v in (linha["desejado"] or {}).items() if (linha["relatado"] or {}).get(k) != v}
    return {
        "dispositivo": slug,
        **{k: linha[k] for k in ("desejado", "desejado_em", "relatado", "relatado_em")},
        "diferenca": diferenca,
    }


@router.get("/{slug}/gemeo", response_model=Gemeo)
def obter_gemeo(banco: Banco, ctx: Leitura, slug: str):
    with banco.connect() as conexao:
        return _gemeo(conexao, ctx, slug)


@router.put("/{slug}/gemeo/desejado", response_model=Gemeo, summary="Definir o estado desejado (comando assíncrono)")
def definir_desejado(request: Request, banco: Banco, ctx: Operacao, slug: str, desejado: dict):
    """
    Grava o desejado e envia o comando `aplicar_desejado` com QoS 1 e sessão
    persistente: se o aparelho estiver offline, o broker entrega na reconexão;
    enquanto ele não relatar os novos valores, a diferença aparece em `diferenca`.
    """
    g = db.gemeo
    with banco.begin() as conexao:
        dispositivo_id = _id_dispositivo(conexao, ctx, slug)
        antes = _gemeo(conexao, ctx, slug)
        stmt = pg_insert(g).values(dispositivo_id=dispositivo_id, desejado=desejado, desejado_em=func.now())
        conexao.execute(
            stmt.on_conflict_do_update(
                index_elements=["dispositivo_id"], set_={"desejado": stmt.excluded.desejado, "desejado_em": func.now()}
            )
        )
        auditar(conexao, ctx, "gemeo", dispositivo_id, "definir_desejado", antes=antes["desejado"], depois=desejado)
    try:
        enviar_comando(
            banco,
            request.app.state.publicar,
            ctx.slug,
            slug,
            {"acao": "aplicar_desejado", "desejado": desejado},
            origem="painel",
        )
    except FalhaAcao as exc:
        raise HTTPException(409, str(exc)) from exc
    with banco.connect() as conexao:
        return _gemeo(conexao, ctx, slug)
