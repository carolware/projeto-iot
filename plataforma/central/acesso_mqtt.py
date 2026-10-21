"""
acesso_mqtt.py — quem pode conectar em cada broker e em quais tópicos
(RF-DIS-02, RF-SEG-04): gera o password_file e o acl_file do Mosquitto a
partir do cadastro.

- Cada dispositivo só publica nos próprios tópicos (telemetria/evento/estado)
  e só lê o próprio tópico de comando. Publicar no tópico de outro é recusado
  pelo broker, na central e nas bordas.
- Credencial revogada sai do arquivo: a conexão seguinte é recusada.
- Só hashes circulam (formato $7$ do Mosquitto); nenhuma senha em claro.

O broker relê os arquivos quando eles mudam (ver infra/mosquitto/iniciar.sh).
Cada nó de borda baixa a parte dele (GET /api/v1/bordas/{slug}/acesso) e a
mantém no disco: sem link, continua autenticando os dispositivos locais.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select

from central import db
from nucleo.credenciais import gravar_se_mudou, hash_senha

log = logging.getLogger("central.acesso")

TIPOS_SIMULADOS = ("estacao", "conector")  # leem o canal de controle da simulação
TOPICO_SIMULACAO = "simulacao/mock"


@dataclass(frozen=True)
class Servico:
    usuario: str
    senha_hash: str
    acl: tuple[str, ...]


def servicos_da_central(senhas: dict[str, str], topico_legado: str) -> list[Servico]:
    """Usuários de serviço do broker central (senhas vindas do ambiente, guardadas só como hash)."""
    acls = {
        "central": (
            "topic read +/+/+/+/telemetria",
            "topic read +/+/+/+/evento",
            "topic read +/+/+/+/estado",
            "topic write +/+/+/+/comando",
            "topic readwrite plataforma/#",
        ),
        # TEMPORÁRIOS até a etapa A5 (painel antigo)
        "ponte": (
            "topic read plataforma/telemetria/#",
            "topic read plataforma/evento/#",
            "topic read plataforma/alarme/#",
            f"topic readwrite {topico_legado}/#",
            "topic write +/+/+/+/comando",
        ),
        "painel": (f"topic read {topico_legado}/#", f"topic write {topico_legado}/+/atuador/alerta"),
        "simulacao": (f"topic write {TOPICO_SIMULACAO}",),
    }
    return [Servico(nome, hash_senha(senha), acls[nome]) for nome, senha in senhas.items() if senha and nome in acls]


def _dispositivos(engine: Engine, inquilino: str | None = None, sites: list[str] | None = None):
    d, s, p, dom, i = db.dispositivo, db.site, db.perfil_dispositivo, db.dominio, db.inquilino
    consulta = (
        select(
            d.c.slug,
            d.c.tipo,
            d.c.credencial_hash,
            i.c.slug.label("inquilino"),
            s.c.slug.label("site"),
            dom.c.slug.label("dominio"),
        )
        .join(i, i.c.id == d.c.inquilino_id)
        .join(s, s.c.id == d.c.site_id)
        .join(p, p.c.id == d.c.perfil_id)
        .join(dom, dom.c.id == p.c.dominio_id)
        .where(d.c.credencial_hash.is_not(None), d.c.revogado_em.is_(None))
        .order_by(d.c.slug)
    )
    if inquilino:
        consulta = consulta.where(i.c.slug == inquilino)
    if sites is not None:
        consulta = consulta.where(s.c.slug.in_(sites))
    with engine.connect() as conexao:
        return conexao.execute(consulta).all()


def _acl_dispositivo(d) -> list[str]:
    base = f"{d.inquilino}/{d.site}/{d.dominio}/{d.slug}"
    linhas = [f"user {d.slug}"]
    linhas += [f"topic write {base}/{tipo}" for tipo in ("telemetria", "evento", "estado")]
    linhas.append(f"topic read {base}/comando")
    if d.tipo in TIPOS_SIMULADOS:
        linhas.append(f"topic read {TOPICO_SIMULACAO}")
    return linhas


def _montar(usuarios: list[tuple[str, str, list[str]]]) -> tuple[str, str]:
    senhas = "".join(f"{u}:{h}\n" for u, h, _ in usuarios)
    acl = "\n".join("\n".join(linhas) + "\n" for _, _, linhas in usuarios)
    return senhas, acl


def arquivos_central(engine: Engine, servicos: list[Servico]) -> tuple[str, str]:
    """Broker central: serviços, a bridge de cada borda e todos os dispositivos."""
    usuarios = [(s.usuario, s.senha_hash, [f"user {s.usuario}", *s.acl]) for s in servicos]
    b, i = db.no_borda, db.inquilino
    with engine.connect() as conexao:
        bordas = conexao.execute(
            select(b.c.slug, b.c.credencial_hash, b.c.sites, i.c.slug.label("inquilino"))
            .join(i, i.c.id == b.c.inquilino_id)
            .where(b.c.credencial_hash.is_not(None))
            .order_by(b.c.slug)
        ).all()
    for borda in bordas:  # a bridge da borda só recebe os comandos dos sites dela
        acl = [f"user {borda.slug}"] + [f"topic read {borda.inquilino}/{site}/+/+/comando" for site in borda.sites]
        usuarios.append((borda.slug, borda.credencial_hash, acl))
    usuarios += [(d.slug, d.credencial_hash, _acl_dispositivo(d)) for d in _dispositivos(engine)]
    return _montar(usuarios)


def arquivos_borda(engine: Engine, borda: str, simulacao_hash: str | None = None) -> tuple[str, str] | None:
    """Broker local de um nó de borda: o próprio serviço da borda e os dispositivos dos sites dela."""
    b, i = db.no_borda, db.inquilino
    with engine.connect() as conexao:
        linha = conexao.execute(
            select(b.c.credencial_hash, b.c.sites, i.c.slug.label("inquilino"))
            .join(i, i.c.id == b.c.inquilino_id)
            .where(b.c.slug == borda)
        ).first()
    if linha is None or not linha.credencial_hash:
        return None
    acl_borda = [f"user {borda}"]
    acl_borda += [f"topic read {linha.inquilino}/+/+/+/{tipo}" for tipo in ("telemetria", "evento", "estado")]
    acl_borda += [f"topic write {linha.inquilino}/{site}/+/+/comando" for site in linha.sites]
    usuarios = [(borda, linha.credencial_hash, acl_borda)]
    if simulacao_hash:
        usuarios.append(("simulacao", simulacao_hash, ["user simulacao", f"topic write {TOPICO_SIMULACAO}"]))
    usuarios += [
        (d.slug, d.credencial_hash, _acl_dispositivo(d))
        for d in _dispositivos(engine, linha.inquilino, list(linha.sites))
    ]
    return _montar(usuarios)


class GestorAcesso:
    """Mantém os arquivos do broker central em dia com o cadastro."""

    def __init__(self, engine: Engine, diretorio: str | None, servicos: list[Servico], simulacao_hash: str | None):
        self.engine = engine
        self.diretorio = Path(diretorio) if diretorio else None
        self.servicos = servicos
        self.simulacao_hash = simulacao_hash

    def atualizar(self) -> None:
        if self.diretorio is None:
            return
        if gravar_se_mudou(self.diretorio, *arquivos_central(self.engine, self.servicos)):
            log.info("Credenciais/ACL do broker central atualizadas")

    def da_borda(self, borda: str) -> tuple[str, str] | None:
        return arquivos_borda(self.engine, borda, self.simulacao_hash)
