"""
painel.py — o que o painel web consome (RF-APP-02/04):

- GET  /api/v1/eu          usuário logado, papéis e inquilino;
- GET  /api/v1/painel      visão por local: leituras atuais, risco (classificado
                           pelas regras do inquilino), satélite, dispositivos e
                           alarmes abertos — uma chamada em vez de dezenas;
- GET  /api/v1/eventos     eventos dos dispositivos, por local ("logs por região");
- WS   /api/v1/tempo-real  telemetria, eventos e alarmes do inquilino em tempo real.

O WebSocket do navegador não envia cabeçalhos: o token vai no parâmetro
`token` (e o admin escolhe o inquilino em `inquilino`).
"""

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select

from central import db
from central.api.comum import Banco, Pagina, ParametrosPagina, paginar
from central.api.dispositivos import _consulta_dispositivos
from central.api.seguranca import Leitura, TokenInvalido, UsuarioAtual
from nucleo.mensagem import agora_utc
from nucleo.regras import SEVERIDADES, mais_severa

router = APIRouter(prefix="/api/v1", tags=["Painel"])

PONTOS_HISTORICO = 12


class Eu(BaseModel):
    usuario: str
    papeis: list[str]
    inquilino: str | None
    cenario: str | None


class Evento(BaseModel):
    tempo: datetime
    dispositivo: str
    site: str
    tipo: str
    dados: dict


@router.get("/eu", response_model=Eu)
def eu(banco: Banco, usuario: UsuarioAtual):
    cenario = None
    if usuario.inquilino:
        with banco.connect() as conexao:
            cenario = conexao.execute(
                select(db.inquilino.c.cenario).where(db.inquilino.c.slug == usuario.inquilino)
            ).scalar()
    return {
        "usuario": usuario.nome,
        "papeis": sorted(usuario.papeis),
        "inquilino": usuario.inquilino,
        "cenario": cenario,
    }


@router.get("/painel", summary="Visão consolidada por local (para o painel web)")
def painel(request: Request, banco: Banco, ctx: Leitura):
    s, d, ue, a, t, e = db.site, db.dispositivo, db.ultimo_estado, db.alarme, db.telemetria, db.evento
    with banco.connect() as conexao:
        cenario = conexao.execute(select(db.inquilino.c.cenario).where(db.inquilino.c.id == ctx.id)).scalar()
        sites = (
            conexao.execute(
                select(s.c.slug, s.c.nome, s.c.lat, s.c.lon, s.c.raio_km)
                .where(s.c.inquilino_id == ctx.id)
                .order_by(s.c.slug)
            )
            .mappings()
            .all()
        )
        dispositivos = conexao.execute(_consulta_dispositivos(ctx).add_columns(d.c.id)).mappings().all()
        ids = [x["id"] for x in dispositivos]
        leituras: dict[uuid.UUID, dict] = defaultdict(dict)
        historico: dict[uuid.UUID, list] = defaultdict(list)
        satelite: dict[uuid.UUID, dict] = {}
        if ids:
            for x in conexao.execute(select(ue).where(ue.c.dispositivo_id.in_(ids))).mappings():
                leituras[x["dispositivo_id"]][x["grandeza"]] = {
                    "valor": x["valor"], "unidade": x["unidade"], "suspeita": x["suspeita"], "tempo": x["tempo"]
                }  # fmt: skip
            for x in conexao.execute(
                select(t.c.dispositivo_id, t.c.valor)
                .where(
                    t.c.dispositivo_id.in_(ids), t.c.grandeza == "fumaca", t.c.tempo >= agora_utc() - timedelta(hours=1)
                )
                .order_by(t.c.tempo.desc())
                .limit(PONTOS_HISTORICO * len(ids))
            ):
                if len(historico[x.dispositivo_id]) < PONTOS_HISTORICO:
                    historico[x.dispositivo_id].append(x.valor)
            for x in conexao.execute(
                select(e.c.dispositivo_id, e.c.tempo, e.c.dados)
                .where(
                    e.c.dispositivo_id.in_(ids),
                    e.c.tipo == "varredura_firms",
                    e.c.tempo >= agora_utc() - timedelta(days=2),
                )
                .order_by(e.c.dispositivo_id, e.c.tempo.desc())
                .distinct(e.c.dispositivo_id)
            ):
                satelite[x.dispositivo_id] = {**x.dados, "atualizado_em": x.tempo}
        abertos = conexao.execute(
            select(s.c.slug, a.c.severidade)
            .join(d, d.c.id == a.c.dispositivo_id)
            .join(s, s.c.id == d.c.site_id)
            .where(a.c.inquilino_id == ctx.id, a.c.estado != "resolvido")
        ).all()

    motor = request.app.state.motor
    alarmes_por_site: dict[str, list[str]] = defaultdict(list)
    for site, severidade in abertos:
        alarmes_por_site[site].append(severidade)

    por_site: dict[str, list[dict]] = defaultdict(list)
    for x in dispositivos:
        valores = {g: v["valor"] for g, v in leituras[x["id"]].items()}
        regras = [r for r in motor.regras(ctx.slug, x["dominio"]) if not r.de_ausencia]
        classificacao = mais_severa(regras, valores) if x["tipo"] == "estacao" else None
        por_site[x["site"]].append(
            {
                "slug": x["slug"],
                "nome": x["nome"],
                "tipo": x["tipo"],
                "dominio": x["dominio"],
                "online": x["online"],
                "revogado": x["revogado"],
                "ultimo_contato_em": x["ultimo_contato_em"],
                "leituras": leituras[x["id"]],
                "risco": classificacao.severidade if classificacao else None,
                "historico_fumaca": list(reversed(historico[x["id"]])),
                "satelite": satelite.get(x["id"]),
            }
        )

    resultado = []
    for site in sites:
        disp = por_site.get(site["slug"], [])
        estacoes = [x for x in disp if x["tipo"] == "estacao"]
        riscos = [x["risco"] for x in estacoes if x["online"] and x["risco"]] + alarmes_por_site.get(site["slug"], [])
        if estacoes and not any(x["online"] for x in estacoes):
            risco = "offline"
        elif riscos:
            risco = max(riscos, key=SEVERIDADES.index)
        else:
            risco = "baixo"
        resultado.append(
            {
                **dict(site),
                "risco": risco,
                "alarmes_abertos": len(alarmes_por_site.get(site["slug"], [])),
                "dispositivos": disp,
            }
        )
    return {"inquilino": ctx.slug, "cenario": cenario, "gerado_em": agora_utc(), "sites": resultado}


@router.get("/eventos", response_model=Pagina[Evento], summary="Eventos dos dispositivos (logs por local)")
def eventos(
    banco: Banco,
    ctx: Leitura,
    pag: ParametrosPagina,
    site: str | None = None,
    dispositivo: str | None = None,
    tipo: str | None = None,
):
    e, d, s = db.evento, db.dispositivo, db.site
    consulta = (
        select(e.c.tempo, d.c.slug.label("dispositivo"), s.c.slug.label("site"), e.c.tipo, e.c.dados)
        .join(d, d.c.id == e.c.dispositivo_id)
        .join(s, s.c.id == d.c.site_id)
        .where(d.c.inquilino_id == ctx.id, e.c.tempo >= agora_utc() - timedelta(days=7))
    )
    if site:
        consulta = consulta.where(s.c.slug == site)
    if dispositivo:
        consulta = consulta.where(d.c.slug == dispositivo)
    if tipo:
        consulta = consulta.where(e.c.tipo == tipo)
    with banco.connect() as conexao:
        return paginar(conexao, consulta, pag, {"tempo": e.c.tempo}, "-tempo")


@router.websocket("/tempo-real")
async def tempo_real(websocket: WebSocket, token: str = Query(...), inquilino: str | None = None):
    """Envia {tipo: telemetria|evento|estado|alarme, ...} do inquilino do token enquanto a conexão durar."""
    try:
        usuario = websocket.app.state.autenticador.validar(token)
    except TokenInvalido:
        await websocket.close(code=4401, reason="token inválido")
        return
    alvo = (inquilino or usuario.inquilino) if usuario.admin else usuario.inquilino
    if not usuario.papeis or not alvo or (inquilino and not usuario.admin and inquilino != usuario.inquilino):
        await websocket.close(code=4403, reason="sem acesso a este inquilino")
        return
    hub = websocket.app.state.hub
    await websocket.accept()
    fila = hub.assinar(alvo)
    await websocket.send_json({"tipo": "conectado", "inquilino": alvo, "usuario": usuario.nome})
    try:
        while True:
            receber = asyncio.ensure_future(websocket.receive_text())  # detecta o fechamento pelo cliente
            enviar = asyncio.ensure_future(fila.get())
            feitos, pendentes = await asyncio.wait({receber, enviar}, return_when=asyncio.FIRST_COMPLETED)
            for tarefa in pendentes:
                tarefa.cancel()
            if receber in feitos:
                receber.result()  # levanta WebSocketDisconnect quando o cliente fecha
                continue
            await websocket.send_json(enviar.result())
    except WebSocketDisconnect:
        pass
    finally:
        hub.cancelar(alvo, fila)
