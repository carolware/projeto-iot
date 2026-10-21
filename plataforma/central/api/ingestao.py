"""
ingestao.py — ingestão por HTTP para quem não usa MQTT (RF-ING-02) e por
lotes para a sincronização dos nós de borda (RF-ING-03). As duas rotas usam o
mesmo pipeline do nucleo que a ingestão MQTT.
"""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central.api.seguranca import AutorizacaoIngestao
from nucleo.ingestao import PipelineIngestao, Resultado
from nucleo.validacao import diretorio_contratos

router = APIRouter(prefix="/api/v1/ingestao", tags=["Ingestão"])

# Autenticação: dispositivo ou nó de borda (HTTP Basic com a própria credencial)
# ou usuário integrador/gestor (Bearer). Cada mensagem só é aceita se a
# credencial puder falar pelo dispositivo dela (ver seguranca.autorizacao_ingestao).

LIMITE_LOTE = 10_000
_EXEMPLO = json.loads((diretorio_contratos() / "exemplos" / "telemetria-estacao.json").read_text(encoding="utf-8"))


class Erro(BaseModel):
    campo: str
    motivo: str


class ResultadoMensagem(BaseModel):
    indice: int
    status: Literal["aceita", "duplicada", "recusada"]
    id_mensagem: str | None = None
    erros: list[Erro] = []


class RespostaLote(BaseModel):
    total: int
    aceitas: int
    duplicadas: int
    recusadas: int
    resultados: list[ResultadoMensagem]


class Lote(BaseModel):
    mensagens: list[Any] = Field(min_length=1, max_length=LIMITE_LOTE)


def _pipeline(request: Request) -> PipelineIngestao:
    return request.app.state.pipeline


def _serializar(r: Resultado) -> dict:
    return {
        "indice": r.indice,
        "status": r.status,
        "id_mensagem": r.id_mensagem,
        "erros": [{"campo": e.campo, "motivo": e.motivo} for e in r.erros],
    }


@router.post(
    "",
    status_code=202,
    response_model=ResultadoMensagem,
    responses={400: {"model": ResultadoMensagem, "description": "Mensagem recusada, com a lista de erros"}},
    summary="Enviar uma mensagem canônica por HTTP",
)
def ingerir(
    request: Request,
    autorizado: AutorizacaoIngestao,
    mensagem: Annotated[Any, Body(description="Mensagem canônica v1.0", examples=[_EXEMPLO])],
):
    [resultado] = _pipeline(request).processar([mensagem], origem="http:/api/v1/ingestao", autorizado=autorizado)
    if resultado.status == "recusada":
        return JSONResponse(status_code=400, content=_serializar(resultado))
    return _serializar(resultado)


@router.post(
    "/lote",
    response_model=RespostaLote,
    summary="Enviar um lote de mensagens (sincronização de nós de borda)",
    description=f"Até {LIMITE_LOTE} mensagens. As válidas são gravadas mesmo que outras sejam recusadas; "
    "o resultado vem por mensagem, na ordem enviada. Reenviar o mesmo lote é seguro (idempotente).",
)
def ingerir_lote(request: Request, autorizado: AutorizacaoIngestao, lote: Lote, via: str | None = None):
    origem = f"lote:{via}" if via else "http:/api/v1/ingestao/lote"
    resultados = _pipeline(request).processar(lote.mensagens, origem=origem, autorizado=autorizado)
    contagem = {s: sum(r.status == s for r in resultados) for s in ("aceita", "duplicada", "recusada")}
    return {
        "total": len(resultados),
        "aceitas": contagem["aceita"],
        "duplicadas": contagem["duplicada"],
        "recusadas": contagem["recusada"],
        "resultados": [_serializar(r) for r in resultados],
    }
