"""
repositorios.py — adaptadores Postgres/TimescaleDB das portas de ingestão do
nucleo (Repository): catálogo, gravação idempotente e fila de inválidas.
"""

import json
import uuid
from collections import defaultdict

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from central import db
from nucleo.ingestao import DispositivoCadastrado, GrandezaPerfil, MensagemEnriquecida, Recusa


class CatalogoPostgres:
    def __init__(self, engine: Engine):
        self.engine = engine

    def buscar_dispositivos(self, slugs: set[str]) -> dict[str, DispositivoCadastrado]:
        if not slugs:
            return {}
        d, p, dom, s, a, i = (
            db.dispositivo,
            db.perfil_dispositivo,
            db.dominio,
            db.site,
            db.area,
            db.inquilino,
        )
        consulta = (
            select(
                d.c.id,
                d.c.slug,
                d.c.revogado_em,
                d.c.perfil_id,
                i.c.slug.label("inquilino"),
                s.c.slug.label("site"),
                a.c.slug.label("area"),
                dom.c.slug.label("dominio"),
                p.c.slug.label("perfil"),
            )
            .join(p, p.c.id == d.c.perfil_id)
            .join(dom, dom.c.id == p.c.dominio_id)
            .join(s, s.c.id == d.c.site_id)
            .join(i, i.c.id == d.c.inquilino_id)
            .outerjoin(a, a.c.id == d.c.area_id)
            .where(d.c.slug.in_(slugs))
        )
        with self.engine.connect() as conexao:
            linhas = conexao.execute(consulta).all()
            grandezas: dict[uuid.UUID, dict[str, GrandezaPerfil]] = defaultdict(dict)
            perfis = {linha.perfil_id for linha in linhas}
            if perfis:
                pg = db.perfil_grandeza
                for g in conexao.execute(select(pg).where(pg.c.perfil_id.in_(perfis))):
                    grandezas[g.perfil_id][g.grandeza] = GrandezaPerfil(g.grandeza, g.unidade, g.valor_min, g.valor_max)
        return {
            linha.slug: DispositivoCadastrado(
                id=str(linha.id),
                slug=linha.slug,
                inquilino=linha.inquilino,
                site=linha.site,
                area=linha.area,
                dominio=linha.dominio,
                perfil=linha.perfil,
                grandezas=grandezas[linha.perfil_id],
                revogado=linha.revogado_em is not None,
            )
            for linha in linhas
        }


class RepositorioPostgres:
    """Grava mensagens de forma idempotente (Idempotent Receiver) numa única transação."""

    TAMANHO_BLOCO = 1000

    def __init__(self, engine: Engine):
        self.engine = engine

    def gravar(self, mensagens: list[MensagemEnriquecida]) -> set[str]:
        if not mensagens:
            return set()
        novas: set[str] = set()
        with self.engine.begin() as conexao:
            # blocos: o Postgres aceita no máximo 65535 parâmetros por comando
            for inicio in range(0, len(mensagens), self.TAMANHO_BLOCO):
                bloco = mensagens[inicio : inicio + self.TAMANHO_BLOCO]
                inseridas = conexao.execute(
                    pg_insert(db.mensagem)
                    .values(
                        [
                            {
                                "id_mensagem": uuid.UUID(m.id_mensagem),
                                "dispositivo_id": uuid.UUID(m.dispositivo.id),
                                "tipo": m.mensagem.tipo,
                                "medido_em": m.mensagem.timestamp,
                                "recebido_em": m.recebido_em,
                                "via": m.mensagem.via,
                                "fonte": m.mensagem.fonte,
                            }
                            for m in bloco
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["id_mensagem"])
                    .returning(db.mensagem.c.id_mensagem)
                ).scalars()
                novas.update(str(i) for i in inseridas)
            gravar = [m for m in mensagens if m.id_mensagem in novas]
            if gravar:
                self._gravar_telemetria(conexao, [m for m in gravar if m.mensagem.tipo == "telemetria"])
                self._gravar_eventos(conexao, [m for m in gravar if m.mensagem.tipo == "evento"])
                self._gravar_estados(conexao, [m for m in gravar if m.mensagem.tipo == "estado"])
                self._atualizar_contato(conexao, gravar)
        return novas

    @staticmethod
    def _gravar_telemetria(conexao, mensagens: list[MensagemEnriquecida]) -> None:
        linhas = [
            {
                "tempo": m.mensagem.timestamp,
                "dispositivo_id": uuid.UUID(m.dispositivo.id),
                "grandeza": medida.grandeza,
                "valor": medida.valor,
                "unidade": medida.unidade,
                "suspeita": medida.suspeita,
                "id_mensagem": uuid.UUID(m.id_mensagem),
            }
            for m in mensagens
            for medida in m.medidas
        ]
        if not linhas:
            return
        conexao.execute(insert(db.telemetria), linhas)

        # último estado: só a leitura mais recente de cada (dispositivo, grandeza);
        # um dado atrasado nunca sobrescreve um mais novo (RF-ING-04 / RF-DAD-02)
        mais_recentes: dict[tuple, dict] = {}
        for linha in linhas:
            chave = (linha["dispositivo_id"], linha["grandeza"])
            if chave not in mais_recentes or linha["tempo"] >= mais_recentes[chave]["tempo"]:
                mais_recentes[chave] = linha
        ue = db.ultimo_estado
        stmt = pg_insert(ue).values(
            [{k: v for k, v in linha.items() if k != "id_mensagem"} for linha in mais_recentes.values()]
        )
        conexao.execute(
            stmt.on_conflict_do_update(
                index_elements=["dispositivo_id", "grandeza"],
                set_={c: stmt.excluded[c] for c in ("valor", "unidade", "suspeita", "tempo")},
                where=ue.c.tempo < stmt.excluded.tempo,
            )
        )

    @staticmethod
    def _gravar_eventos(conexao, mensagens: list[MensagemEnriquecida]) -> None:
        if not mensagens:
            return
        conexao.execute(
            insert(db.evento),
            [
                {
                    "tempo": m.mensagem.timestamp,
                    "dispositivo_id": uuid.UUID(m.dispositivo.id),
                    "tipo": str(m.mensagem.dados.get("evento", "evento")),
                    "comando_id": _uuid_ou_none(m.mensagem.dados.get("comando_id")),
                    "id_mensagem": uuid.UUID(m.id_mensagem),
                    "dados": m.mensagem.dados,
                }
                for m in mensagens
            ],
        )

    @staticmethod
    def _gravar_estados(conexao, mensagens: list[MensagemEnriquecida]) -> None:
        """Estado relatado alimenta o gêmeo; só o mais recente por dispositivo vale."""
        mais_recentes: dict[str, MensagemEnriquecida] = {}
        for m in mensagens:
            atual = mais_recentes.get(m.dispositivo.id)
            if atual is None or m.mensagem.timestamp >= atual.mensagem.timestamp:
                mais_recentes[m.dispositivo.id] = m
        g = db.gemeo
        for m in mais_recentes.values():
            stmt = pg_insert(g).values(
                dispositivo_id=uuid.UUID(m.dispositivo.id),
                relatado=m.mensagem.dados,
                relatado_em=m.mensagem.timestamp,
            )
            conexao.execute(
                stmt.on_conflict_do_update(
                    index_elements=["dispositivo_id"],
                    set_={"relatado": stmt.excluded.relatado, "relatado_em": stmt.excluded.relatado_em},
                    where=(g.c.relatado_em.is_(None)) | (g.c.relatado_em < stmt.excluded.relatado_em),
                )
            )

    @staticmethod
    def _atualizar_contato(conexao, mensagens: list[MensagemEnriquecida]) -> None:
        ultimo: dict[str, object] = {}
        for m in mensagens:
            if m.dispositivo.id not in ultimo or m.mensagem.timestamp > ultimo[m.dispositivo.id]:
                ultimo[m.dispositivo.id] = m.mensagem.timestamp
        d = db.dispositivo
        for dispositivo_id, quando in ultimo.items():
            conexao.execute(
                update(d)
                .where(d.c.id == uuid.UUID(dispositivo_id))
                .values(ultimo_contato_em=func.greatest(func.coalesce(d.c.ultimo_contato_em, quando), quando))
            )


class FilaInvalidasPostgres:
    """Dead Letter Channel: nenhuma mensagem recusada se perde em silêncio."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def registrar(self, recusas: list[Recusa]) -> None:
        if not recusas:
            return
        with self.engine.begin() as conexao:
            conexao.execute(
                insert(db.dlq),
                [
                    {
                        "origem": r.origem,
                        "payload": r.payload
                        if isinstance(r.payload, str)
                        else json.dumps(r.payload, ensure_ascii=False),
                        "motivo": "; ".join(str(e) for e in r.erros),
                    }
                    for r in recusas
                ],
            )


def _uuid_ou_none(valor: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valor)) if valor else None
    except ValueError:
        return None
