"""
telemetria.py — consulta de séries temporais além de um dispositivo só
(RF-DAD-01): por área, site, dispositivo e grandeza, num intervalo, em bruto
ou agregada por hora (RF-PRO-04). Sempre restrita ao inquilino de quem pede.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from central import db
from central.api.comum import Banco, Pagina, ParametrosPagina, paginar
from central.api.dispositivos import PontoTelemetria
from central.api.seguranca import Leitura

router = APIRouter(prefix="/api/v1", tags=["Dados"])


class PontoComDispositivo(PontoTelemetria):
    dispositivo: str
    site: str
    area: str | None = None


@router.get("/telemetria", response_model=Pagina[PontoComDispositivo], summary="Séries por área, site e intervalo")
def consultar(
    banco: Banco,
    ctx: Leitura,
    pag: ParametrosPagina,
    area: Annotated[str | None, Query(description="slug da área (ex.: talhão, praça)")] = None,
    site: str | None = None,
    dispositivo: str | None = None,
    grandeza: str | None = None,
    inicio: Annotated[datetime | None, Query(description="ISO 8601 com fuso; inclusivo")] = None,
    fim: Annotated[datetime | None, Query(description="ISO 8601 com fuso; exclusivo")] = None,
    agregacao: Annotated[Literal["bruto", "1h"], Query(description="1h = média/mín/máx por hora")] = "bruto",
):
    if inicio and fim and inicio >= fim:
        raise HTTPException(422, "inicio deve ser anterior a fim")
    d, s, a = db.dispositivo, db.site, db.area
    if agregacao == "bruto":
        t = db.telemetria
        tempo = t.c.tempo
        colunas = [t.c.tempo, t.c.grandeza, t.c.valor, t.c.unidade, t.c.suspeita]
    else:
        t = db.telemetria_1h
        tempo = t.c.hora
        colunas = [
            t.c.hora.label("tempo"),
            t.c.grandeza,
            t.c.media.label("valor"),
            t.c.minimo,
            t.c.maximo,
            t.c.amostras,
        ]
    consulta = (
        select(*colunas, d.c.slug.label("dispositivo"), s.c.slug.label("site"), a.c.slug.label("area"))
        .join(d, d.c.id == t.c.dispositivo_id)
        .join(s, s.c.id == d.c.site_id)
        .outerjoin(a, a.c.id == d.c.area_id)
        .where(d.c.inquilino_id == ctx.id)
    )
    for coluna, valor in ((a.c.slug, area), (s.c.slug, site), (d.c.slug, dispositivo), (t.c.grandeza, grandeza)):
        if valor:
            consulta = consulta.where(coluna == valor)
    if inicio:
        consulta = consulta.where(tempo >= inicio)
    if fim:
        consulta = consulta.where(tempo < fim)
    with banco.connect() as conexao:
        return paginar(
            conexao, consulta, pag, {"tempo": tempo, "grandeza": t.c.grandeza, "dispositivo": d.c.slug}, "-tempo"
        )
