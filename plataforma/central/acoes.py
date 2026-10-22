"""
acoes.py — ações disparadas por alarmes (RF-PRO-03), uma estratégia por tipo
(padrão Strategy): incluir um tipo novo é registrar uma classe nova, sem mexer
no motor de regras.

    comando    envia um comando canônico a um dispositivo e registra a resposta (RF-DIS-05)
    webhook    POST do alarme em uma URL externa (RF-APP-03)
    publicar   publica o alarme em um tópico MQTT
    notificar  e-mail / SMS / WhatsApp — simulado: registra a mensagem no log (RF-APP-05)

Toda execução fica em `execucao_acao`. Falhou? Nova tentativa com espera
crescente (2 s, 4 s, 8 s… até 5 min), no máximo MAX_TENTATIVAS (Retry com
backoff): um destino fora do ar não derruba nem trava a plataforma.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import requests
from fastapi.encoders import jsonable_encoder
from sqlalchemy import Engine, insert, select, update

from central import db
from central.metricas import ACOES
from nucleo.mensagem import agora_utc, nova_mensagem
from nucleo.topicos import montar_topico

log = logging.getLogger("central.acoes")

MAX_TENTATIVAS = 6
ESPERA_MAXIMA = timedelta(minutes=5)

Publicador = Callable[[str, dict], None]


@dataclass(frozen=True)
class ContextoAcao:
    """O alarme como as ações o veem (mesmo formato publicado no barramento)."""

    alarme: dict
    config: dict = field(default_factory=dict)


class FalhaAcao(Exception):
    pass


class Estrategia(Protocol):
    def executar(self, ctx: ContextoAcao) -> None: ...


# ──────────────────────────────────────────────
# Estratégias
# ──────────────────────────────────────────────


class AcaoComando:
    """Envia um comando canônico ao dispositivo-alvo (padrão: o atuador do site do alarme)."""

    ALVO_PADRAO = "atuador-{site}-01"

    def __init__(self, engine: Engine, publicar: Publicador):
        self.engine = engine
        self.publicar = publicar

    def executar(self, ctx: ContextoAcao) -> None:
        alvo = ctx.config.get("alvo", self.ALVO_PADRAO).format(site=ctx.alarme["site"])
        dados = {
            "acao": ctx.config["acao"],
            "severidade": ctx.alarme["severidade"],
            "alarme_id": ctx.alarme["id"],
            "regra": ctx.alarme.get("regra"),
            "origem": "central",
            "leituras": ctx.alarme["dados"].get("leituras", {}),
        }
        enviar_comando(self.engine, self.publicar, ctx.alarme["inquilino"], alvo, dados, alarme_id=ctx.alarme["id"])


class AcaoWebhook:
    def __init__(self, post: Callable[..., requests.Response] = requests.post):
        self.post = post

    def executar(self, ctx: ContextoAcao) -> None:
        try:
            resposta = self.post(ctx.config["url"], json=jsonable_encoder(ctx.alarme), timeout=5)
        except requests.RequestException as exc:
            raise FalhaAcao(f"webhook inacessível: {exc}") from exc
        if not 200 <= resposta.status_code < 300:
            raise FalhaAcao(f"webhook respondeu {resposta.status_code}")


class AcaoPublicar:
    def __init__(self, publicar: Publicador):
        self.publicar = publicar

    def executar(self, ctx: ContextoAcao) -> None:
        self.publicar(ctx.config["topico"], jsonable_encoder(ctx.alarme))


class AcaoNotificar:
    """Notificação simulada: a plataforma não integra gateway de SMS/e-mail real nesta fase."""

    def __init__(self):
        self.enviadas: list[dict] = []

    def executar(self, ctx: ContextoAcao) -> None:
        a = ctx.alarme
        texto = (
            f"[Sentinela] Alarme {a['severidade'].upper()} em {a['site']} ({a['dispositivo']}): "
            f"{a.get('regra') or a['dados'].get('resumo', '')}"
        )
        destino = mascarar(str(ctx.config["destino"]))
        self.enviadas.append({"canal": ctx.config["canal"], "destino": destino, "texto": texto})
        log.warning("[%s → %s] %s", ctx.config["canal"].upper(), destino, texto)


def mascarar(destino: str) -> str:
    """Pseudonimiza o destino nos logs (LGPD, RF-DAD-05): mantém só o fim do número/e-mail."""
    if "@" in destino:
        usuario, dominio = destino.split("@", 1)
        return f"{usuario[:1]}***@{dominio}"
    return f"***{destino[-4:]}" if len(destino) > 4 else "***"


# ──────────────────────────────────────────────
# Comandos (usado pela ação e pela API)
# ──────────────────────────────────────────────


def enviar_comando(
    engine: Engine,
    publicar: Publicador,
    inquilino: str,
    dispositivo: str,
    dados: dict,
    alarme_id: str | None = None,
    origem: str = "regra",
) -> str:
    """Registra e publica um comando canônico; devolve o id (= id_mensagem do comando)."""
    d, s, p, dom, i = db.dispositivo, db.site, db.perfil_dispositivo, db.dominio, db.inquilino
    with engine.begin() as conexao:
        alvo = conexao.execute(
            select(d.c.id, s.c.slug.label("site"), dom.c.slug.label("dominio"))
            .join(s, s.c.id == d.c.site_id)
            .join(p, p.c.id == d.c.perfil_id)
            .join(dom, dom.c.id == p.c.dominio_id)
            .join(i, i.c.id == d.c.inquilino_id)
            .where(d.c.slug == dispositivo, i.c.slug == inquilino, d.c.revogado_em.is_(None))
        ).first()
        if alvo is None:
            raise FalhaAcao(f"dispositivo-alvo {dispositivo!r} não existe no inquilino {inquilino!r}")
        mensagem = nova_mensagem(dispositivo, "comando", dados=dados)
        conexao.execute(
            insert(db.comando).values(
                id=uuid.UUID(mensagem["id_mensagem"]),
                dispositivo_id=alvo.id,
                alarme_id=uuid.UUID(alarme_id) if alarme_id else None,
                acao=dados["acao"],
                payload=mensagem,
                origem=origem,
                estado="enviado",
                enviado_em=agora_utc(),
            )
        )
    publicar(montar_topico(inquilino, alvo.site, alvo.dominio, dispositivo, "comando"), mensagem)
    return mensagem["id_mensagem"]


# ──────────────────────────────────────────────
# Executor
# ──────────────────────────────────────────────


class ExecutorAcoes:
    def __init__(
        self,
        engine: Engine,
        estrategias: dict[str, Estrategia],
        relogio: Callable[[], datetime] = agora_utc,
    ):
        self.engine = engine
        self.estrategias = estrategias
        self.relogio = relogio

    def executar(self, alarme: dict, acoes: list[dict]) -> None:
        """Registra e executa cada ação; as que falharem ficam agendadas para nova tentativa."""
        for acao in acoes:
            if acao["tipo"] not in self.estrategias:
                continue
            config = {k: v for k, v in acao.items() if k not in ("tipo", "id")}
            with self.engine.begin() as conexao:
                execucao_id = conexao.execute(
                    insert(db.execucao_acao)
                    .values(
                        alarme_id=uuid.UUID(alarme["id"]),
                        regra_acao_id=uuid.UUID(acao["id"]) if acao.get("id") else None,
                        tipo=acao["tipo"],
                        config=config,
                        tentativas=0,
                        status="pendente",
                        proxima_tentativa_em=self.relogio(),
                    )
                    .returning(db.execucao_acao.c.id)
                ).scalar_one()
            self._tentar(execucao_id, acao["tipo"], ContextoAcao(alarme, config), 0)

    def reprocessar_pendentes(self, carregar_alarme: Callable[[uuid.UUID], dict | None]) -> int:
        """Tenta de novo as execuções cujo horário de retentativa já chegou."""
        e = db.execucao_acao
        with self.engine.connect() as conexao:
            pendentes = conexao.execute(
                select(e.c.id, e.c.alarme_id, e.c.tipo, e.c.config, e.c.tentativas).where(
                    e.c.status == "pendente", e.c.proxima_tentativa_em <= self.relogio()
                )
            ).all()
        for p in pendentes:
            alarme = carregar_alarme(p.alarme_id)
            if alarme is not None:
                self._tentar(p.id, p.tipo, ContextoAcao(alarme, p.config), p.tentativas)
        return len(pendentes)

    def _tentar(self, execucao_id: uuid.UUID, tipo: str, ctx: ContextoAcao, tentativas: int) -> None:
        e = db.execucao_acao
        tentativas += 1
        try:
            self.estrategias[tipo].executar(ctx)
        except Exception as exc:  # qualquer falha externa vira retentativa, nunca derruba o motor
            ACOES.labels(tipo, "falha").inc()
            desistir = tentativas >= MAX_TENTATIVAS
            espera = min(ESPERA_MAXIMA, timedelta(seconds=2**tentativas))
            valores = {
                "tentativas": tentativas,
                "status": "falhou" if desistir else "pendente",
                "ultimo_erro": str(exc)[:500],
                "proxima_tentativa_em": None if desistir else self.relogio() + espera,
            }
            log.warning("Ação %s do alarme %s falhou (tentativa %d): %s", tipo, ctx.alarme["id"][:8], tentativas, exc)
        else:
            ACOES.labels(tipo, "sucesso").inc()
            valores = {"tentativas": tentativas, "status": "sucesso", "executado_em": self.relogio()}
        with self.engine.begin() as conexao:
            conexao.execute(update(e).where(e.c.id == execucao_id).values(**valores))
