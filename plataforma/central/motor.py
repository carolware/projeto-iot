"""
motor.py — motor de regras e ciclo de vida dos alarmes da central (RF-PRO-02/03/05).

Recebe, pelo barramento interno (Observer), cada mensagem que a ingestão
aceitou e:

- telemetria: avalia as regras do domínio. Regras de escopo "borda" só são
  avaliadas aqui quando a mensagem NÃO passou por um nó de borda (zonas de
  conexão direta): para as outras, quem decide é a borda, e a central recebe o
  evento `alarme_local`. Abre, escala e resolve alarmes automaticamente;
- evento `alarme_local` (da borda): vira alarme com o horário em que ocorreu,
  mesmo que chegue horas depois (RF-PRO-05);
- evento com `comando_id`: registra a resposta do dispositivo ao comando;
  `reconhecido` vindo de um terminal reconhece o alarme entregue a ele;
- qualquer mensagem: prova que o dispositivo voltou e resolve alarmes de
  ausência de dados.

Alarmes abertos alcançam outros inquilinos pelas assinaturas de alerta: a
prefeitura cadastra um ponto, um raio e o domínio que quer acompanhar; o alarme
da brigada dentro da área vira um alarme da prefeitura, e os terminais dela
recebem o comando `exibir_alerta`. O isolamento entre inquilinos (RF-SEG-03)
fica preservado: a prefeitura só vê a entrega, não os dados da brigada.
"""

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from central import db
from central.acoes import ExecutorAcoes, FalhaAcao, Publicador, enviar_comando
from nucleo.geo import distancia_km
from nucleo.ingestao import MensagemEnriquecida, Recusa
from nucleo.mensagem import agora_utc
from nucleo.regras import SEVERIDADES, Regra, concorrem, severidade_atinge, sustentada, vencedoras

log = logging.getLogger("central.motor")

ATRASO_MAX = timedelta(minutes=5)  # leitura mais velha que isso não abre alarme "atual" (RF-ING-04)
CACHE_REGRAS_S = 5.0
CICLO_AGENDADOR_S = 5.0
VERIFICAR_RETORNO_S = 5.0
ROTULO_SEVERIDADE = {
    "medio": "médio",
    "alto": "alto",
    "critico": "crítico",
}  # no máximo uma consulta "voltou a enviar?" por dispositivo nesse intervalo


@dataclass(frozen=True)
class Origem:
    """Dispositivo de onde o alarme nasce."""

    dispositivo_id: uuid.UUID
    dispositivo: str
    inquilino: str
    site: str
    dominio: str


class MotorRegras:
    def __init__(
        self,
        engine: Engine,
        executor: ExecutorAcoes,
        publicar: Publicador,
        relogio: Callable[[], datetime] = agora_utc,
    ):
        self.engine = engine
        self.executor = executor
        self.publicar = publicar
        self.relogio = relogio
        self._cache: dict[tuple[str, str], tuple[float, list[Regra]]] = {}
        self._retorno_verificado: dict[uuid.UUID, float] = {}
        self._fila: queue.Queue[MensagemEnriquecida | None] = queue.Queue()
        self._parar = threading.Event()
        self._threads: list[threading.Thread] = []

    # ── Barramento (Observer) ───────────────────────────────────────────────

    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None:
        if self._threads:
            self._fila.put(mensagem)
        else:
            self.processar(mensagem)  # modo síncrono (testes, scripts)

    def publicar_recusada(self, recusa: Recusa) -> None:
        pass

    def iniciar(self) -> None:
        self._threads = [
            threading.Thread(target=self._consumir, name="motor-regras", daemon=True),
            threading.Thread(target=self._agendar, name="motor-agendador", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def parar(self) -> None:
        self._parar.set()
        self._fila.put(None)

    def _consumir(self) -> None:
        while (mensagem := self._fila.get()) is not None:
            try:
                self.processar(mensagem)
            except Exception:
                log.exception("Erro no motor de regras (mensagem %s)", mensagem.id_mensagem)

    def _agendar(self) -> None:
        while not self._parar.wait(CICLO_AGENDADOR_S):
            try:
                self.verificar_ausencias()
                self.executor.reprocessar_pendentes(self.carregar_alarme)
            except Exception:
                log.exception("Erro no agendador do motor")

    # ── Entrada ─────────────────────────────────────────────────────────────

    def processar(self, m: MensagemEnriquecida) -> None:
        d = m.dispositivo
        origem = Origem(uuid.UUID(d.id), d.slug, d.inquilino, d.site, d.dominio)
        self._resolver_ausencias(origem.dispositivo_id)
        if m.mensagem.tipo == "telemetria":
            self._avaliar_telemetria(m, origem)
        elif m.mensagem.tipo == "evento":
            dados = m.mensagem.dados
            if dados.get("evento") == "alarme_local":
                self._alarme_da_borda(m, origem)
            if dados.get("comando_id"):
                self._registrar_resposta(m, origem)

    def invalidar_regras(self) -> None:
        self._cache.clear()

    # ── Telemetria ──────────────────────────────────────────────────────────

    def _avaliar_telemetria(self, m: MensagemEnriquecida, origem: Origem) -> None:
        if self.relogio() - m.mensagem.timestamp > ATRASO_MAX:
            return
        regras = [r for r in self.regras(origem.inquilino, origem.dominio) if r.gera_alarme and not r.de_ausencia]
        if not regras:
            return
        valores = {medida.grandeza: medida.valor for medida in m.medidas}

        por_id = {r.id: r for r in regras}
        for alarme in self._abertos(origem.dispositivo_id):
            regra = por_id.get(alarme["regra_id"])
            if regra and regra.grandezas() <= valores.keys() and not regra.atende(valores):
                self.resolver(alarme["id"], "automatico: condição deixou de valer")

        avaliadas = [r for r in regras if r.escopo == "central" or m.mensagem.via is None]
        atendidas = [
            r
            for r in avaliadas
            if r.atende(valores)
            and (r.janela_s == 0 or self._sustentada(r, origem.dispositivo_id, m.mensagem.timestamp))
        ]
        for alvo in vencedoras(atendidas):
            dados = {"leituras": valores, "mensagem_origem": m.id_mensagem, "via": m.mensagem.via}
            self._abrir(alvo, origem, m.mensagem.timestamp, dados, origem_alarme="central")

    def _sustentada(self, regra: Regra, dispositivo_id: uuid.UUID, fim: datetime) -> bool:
        t, p, d = db.telemetria, db.perfil_dispositivo, db.dispositivo
        with self.engine.connect() as conexao:
            intervalo = conexao.execute(
                select(p.c.intervalo_esperado_s).join(d, d.c.perfil_id == p.c.id).where(d.c.id == dispositivo_id)
            ).scalar_one()
            tolerancia = timedelta(seconds=intervalo * 1.5)
            # inclui leituras um pouco antes da janela: a última delas diz se a condição já valia no início
            linhas = conexao.execute(
                select(t.c.tempo, t.c.grandeza, t.c.valor).where(
                    t.c.dispositivo_id == dispositivo_id,
                    t.c.grandeza.in_(regra.grandezas()),
                    t.c.tempo >= fim - timedelta(seconds=regra.janela_s) - tolerancia,
                    t.c.tempo <= fim,
                )
            ).all()
        serie: dict[datetime, dict[str, float]] = {}
        for tempo, grandeza, valor in linhas:
            serie.setdefault(tempo, {})[grandeza] = valor
        return sustentada(regra, sorted(serie.items()), fim, tolerancia)

    # ── Eventos ─────────────────────────────────────────────────────────────

    def _alarme_da_borda(self, m: MensagemEnriquecida, origem: Origem) -> None:
        dados = m.mensagem.dados
        regra = next((r for r in self.regras(origem.inquilino, origem.dominio) if r.nome == dados.get("regra")), None)
        if regra is None:
            # regra local que a central não conhece (ex.: arquivo padrão da borda): registra mesmo assim
            regra = Regra(
                dados.get("regra") or "regra-local",
                dados.get("severidade", "alto"),
                {"todas": []},
                ({"tipo": "alarme"},),
                escopo="borda",
            )
        self._registrar_comandos_da_borda(dados.get("comandos", []), origem, m.mensagem.timestamp)
        info = {
            "leituras": dados.get("leituras", {}),
            "mensagem_origem": dados.get("mensagem_origem"),
            "via": m.mensagem.via,
            "decidido_na_borda": True,
        }
        # a borda já mandou o comando ao atuador: aqui só as demais ações (notificar, webhook…)
        self._abrir(regra, origem, m.mensagem.timestamp, info, origem_alarme="borda", pular=("comando",))

    def _registrar_comandos_da_borda(self, comandos: list, origem: Origem, quando: datetime) -> None:
        d = db.dispositivo
        for comando in comandos:
            if not isinstance(comando, dict) or "id" not in comando:
                continue
            with self.engine.begin() as conexao:
                alvo = conexao.execute(select(d.c.id).where(d.c.slug == comando.get("dispositivo"))).scalar()
                if alvo is None:
                    continue
                conexao.execute(
                    pg_insert(db.comando)
                    .values(
                        id=uuid.UUID(comando["id"]),
                        dispositivo_id=alvo,
                        acao=comando.get("acao", "?"),
                        payload=comando,
                        origem="borda",
                        estado="enviado",
                        enviado_em=quando,
                    )
                    .on_conflict_do_nothing()
                )

    def _registrar_resposta(self, m: MensagemEnriquecida, origem: Origem) -> None:
        dados = m.mensagem.dados
        try:
            comando_id = uuid.UUID(str(dados["comando_id"]))
        except ValueError:
            return
        evento = dados.get("evento")
        c = db.comando
        with self.engine.begin() as conexao:
            conexao.execute(
                update(c)
                .where(c.c.id == comando_id, c.c.dispositivo_id == origem.dispositivo_id)
                .values(
                    estado="confirmado" if evento in ("executado", "recebido", "reconhecido") else "falhou",
                    resposta=dados,
                    respondido_em=m.mensagem.timestamp,
                )
            )
        if evento == "reconhecido" and dados.get("alarme_id"):
            alarme = self.carregar_alarme(dados["alarme_id"])
            if alarme and alarme["inquilino"] == origem.inquilino:  # só reconhece alarme do próprio inquilino
                self.reconhecer(alarme["id"], f"{origem.dispositivo}:{dados.get('por', 'operador')}")

    # ── Ausência de dados ───────────────────────────────────────────────────

    def verificar_ausencias(self) -> int:
        """Abre alarme para dispositivo calado há mais que `ausencia.segundos` (agendado)."""
        r, dom, i = db.regra, db.dominio, db.inquilino
        with self.engine.connect() as conexao:
            linhas = conexao.execute(
                select(i.c.slug.label("inquilino"), dom.c.slug.label("dominio"))
                .select_from(r)
                .join(dom, dom.c.id == r.c.dominio_id)
                .join(i, i.c.id == r.c.inquilino_id)
                .where(r.c.ativa, r.c.condicao.op("?")("ausencia"))
                .distinct()
            ).all()
        abertos = 0
        for inquilino, dominio in linhas:
            for regra in self.regras(inquilino, dominio):
                if regra.de_ausencia and regra.gera_alarme:
                    abertos += self._abrir_ausencias(regra, inquilino, dominio)
        return abertos

    def _abrir_ausencias(self, regra: Regra, inquilino: str, dominio: str) -> int:
        cfg = regra.condicao["ausencia"]
        d, p, dom, s, i = db.dispositivo, db.perfil_dispositivo, db.dominio, db.site, db.inquilino
        limite = self.relogio() - timedelta(seconds=cfg["segundos"])
        consulta = (
            select(d.c.id, d.c.slug, s.c.slug.label("site"), d.c.ultimo_contato_em)
            .join(p, p.c.id == d.c.perfil_id)
            .join(dom, dom.c.id == p.c.dominio_id)
            .join(s, s.c.id == d.c.site_id)
            .join(i, i.c.id == d.c.inquilino_id)
            .where(
                i.c.slug == inquilino,
                dom.c.slug == dominio,
                d.c.revogado_em.is_(None),
                d.c.ultimo_contato_em < limite,
            )
        )
        if cfg.get("perfil"):
            consulta = consulta.where(p.c.slug == cfg["perfil"])
        if regra.id:
            # silêncio contínuo é UMA ocorrência: quem já tem o alarme aberto fica de fora
            a = db.alarme
            ja_aberto = select(a.c.dispositivo_id).where(
                a.c.regra_id == uuid.UUID(regra.id), a.c.estado != "resolvido", a.c.origem_alarme_id.is_(None)
            )
            consulta = consulta.where(d.c.id.not_in(ja_aberto))
        with self.engine.connect() as conexao:
            calados = conexao.execute(consulta).all()
        novos = 0
        for disp in calados:
            origem = Origem(disp.id, disp.slug, inquilino, disp.site, dominio)
            ocorrido = disp.ultimo_contato_em + timedelta(seconds=cfg["segundos"])
            dados = {"ultimo_contato_em": disp.ultimo_contato_em, "silencio_s": cfg["segundos"]}
            if self._abrir(regra, origem, ocorrido, dados, origem_alarme="central") is not None:
                novos += 1
                self._retorno_verificado.pop(disp.id, None)  # a próxima mensagem dele já resolve
        return novos

    def _resolver_ausencias(self, dispositivo_id: uuid.UUID) -> None:
        agora = time.monotonic()
        if agora - self._retorno_verificado.get(dispositivo_id, -VERIFICAR_RETORNO_S) < VERIFICAR_RETORNO_S:
            return
        self._retorno_verificado[dispositivo_id] = agora
        a, r = db.alarme, db.regra
        with self.engine.connect() as conexao:
            ids = conexao.execute(
                select(a.c.id)
                .join(r, r.c.id == a.c.regra_id)
                .where(
                    a.c.dispositivo_id == dispositivo_id,
                    a.c.estado != "resolvido",
                    a.c.origem_alarme_id.is_(None),
                    r.c.condicao.op("?")("ausencia"),
                )
            ).scalars()
            ids = list(ids)
        for alarme_id in ids:
            self.resolver(str(alarme_id), "automatico: dispositivo voltou a enviar dados")

    # ── Ciclo de vida ───────────────────────────────────────────────────────

    def _abrir(
        self,
        regra: Regra,
        origem: Origem,
        ocorrido_em: datetime,
        dados: dict,
        origem_alarme: str,
        pular: tuple[str, ...] = (),
    ) -> dict | None:
        """Abre o alarme (ou conta mais uma ocorrência do que já está aberto). Devolve o alarme novo ou None."""
        a = db.alarme
        regra_id = uuid.UUID(regra.id) if regra.id else None
        aberto = next(
            (
                al
                for al in self._abertos(origem.dispositivo_id)
                if al["regra_id"] == regra.id and (regra_id or al["dados"].get("regra_nome") == regra.nome)
            ),
            None,
        )
        if aberto:
            with self.engine.begin() as conexao:
                conexao.execute(
                    update(a)
                    .where(a.c.id == uuid.UUID(aberto["id"]))
                    .values(ocorrencias=a.c.ocorrencias + 1, atualizado_em=func.now())
                )
            return None

        dados = jsonable_encoder(dados | {"origem": origem_alarme, "regra_nome": regra.nome} | self._satelite(origem))
        with self.engine.begin() as conexao:
            novo_id = conexao.execute(
                pg_insert(a)
                .values(
                    inquilino_id=select(db.inquilino.c.id)
                    .where(db.inquilino.c.slug == origem.inquilino)
                    .scalar_subquery(),
                    regra_id=regra_id,
                    dispositivo_id=origem.dispositivo_id,
                    severidade=regra.severidade,
                    estado="ativo",
                    ocorrido_em=ocorrido_em,
                    dados=dados,
                )
                .on_conflict_do_nothing()
                .returning(a.c.id)
            ).scalar()
        if novo_id is None:  # corrida com outra thread: já existe
            return None

        alarme = self.carregar_alarme(novo_id)
        log.warning(
            "ALARME %s aberto: %s em %s/%s (%s)",
            regra.severidade.upper(),
            regra.nome,
            origem.inquilino,
            origem.dispositivo,
            origem_alarme,
        )
        self._publicar_alarme("aberto", alarme)

        # escalada: um alarme mais severo encerra os menos severos de regras concorrentes
        por_id = {r.id: r for r in self.regras(origem.inquilino, origem.dominio)}
        for antigo in self._abertos(origem.dispositivo_id):
            regra_antiga = por_id.get(antigo["regra_id"])
            mais_fraco = SEVERIDADES.index(antigo["severidade"]) < SEVERIDADES.index(regra.severidade)
            if antigo["id"] != alarme["id"] and mais_fraco and (regra_antiga is None or concorrem(regra, regra_antiga)):
                self.resolver(antigo["id"], f"escalado para {regra.severidade}")

        acoes = [ac for ac in regra.acoes if ac.get("tipo") not in ("alarme", *pular)]
        self.executor.executar(alarme, acoes)
        self._entregar_assinaturas(alarme)
        return alarme

    def reconhecer(self, alarme_id: str, por: str) -> dict | None:
        a = db.alarme
        with self.engine.begin() as conexao:
            alterado = conexao.execute(
                update(a)
                .where(a.c.id == uuid.UUID(alarme_id), a.c.estado == "ativo")
                .values(
                    estado="reconhecido", reconhecido_em=self.relogio(), reconhecido_por=por, atualizado_em=func.now()
                )
                .returning(a.c.id)
            ).scalar()
        if alterado is None:
            return None
        alarme = self.carregar_alarme(alarme_id)
        self._publicar_alarme("reconhecido", alarme)
        return alarme

    def resolver(self, alarme_id: str, por: str) -> dict | None:
        a = db.alarme
        with self.engine.begin() as conexao:
            alterado = conexao.execute(
                update(a)
                .where(a.c.id == uuid.UUID(alarme_id), a.c.estado != "resolvido")
                .values(estado="resolvido", resolvido_em=self.relogio(), resolvido_por=por, atualizado_em=func.now())
                .returning(a.c.id)
            ).scalar()
        if alterado is None:
            return None
        alarme = self.carregar_alarme(alarme_id)
        log.info("Alarme %s resolvido (%s)", alarme_id[:8], por)
        self._publicar_alarme("resolvido", alarme)

        # alarmes entregues às prefeituras seguem a origem, e os terminais delas são avisados
        with self.engine.connect() as conexao:
            entregas = conexao.execute(
                select(a.c.id).where(a.c.origem_alarme_id == uuid.UUID(alarme_id), a.c.estado != "resolvido")
            ).scalars()
            entregas = [str(e) for e in entregas]
        for entrega_id in entregas:
            entrega = self.resolver(entrega_id, f"origem resolvida ({por})")
            if entrega:
                self._comandar_terminais(entrega, "encerrar_alerta")
        return alarme

    # ── Assinaturas de alerta (entre inquilinos) ────────────────────────────

    def _entregar_assinaturas(self, alarme: dict) -> None:
        if alarme["origem_alarme_id"] or alarme.get("lat") is None:
            return
        s, a, i = db.assinatura_alerta, db.alarme, db.inquilino
        with self.engine.connect() as conexao:
            assinaturas = conexao.execute(
                select(s, i.c.slug.label("assinante"))
                .join(i, i.c.id == s.c.inquilino_id)
                .where(s.c.ativa, s.c.dominio_slug == alarme["dominio"], i.c.slug != alarme["inquilino"])
            ).mappings()
            assinaturas = [dict(x) for x in assinaturas]
        for ass in assinaturas:
            distancia = distancia_km(ass["lat"], ass["lon"], alarme["lat"], alarme["lon"])
            if distancia > ass["raio_km"] or not severidade_atinge(alarme["severidade"], ass["severidade_min"]):
                continue
            dominio = (alarme.get("dominio_nome") or alarme["dominio"]).lower()
            local = alarme.get("site_nome") or alarme["site"]
            resumo = (
                f"Risco {ROTULO_SEVERIDADE[alarme['severidade']]} de {dominio} em {local} "
                f"a {distancia:.0f} km ({ass['nome'] or 'área monitorada'})"
            )
            dados = {
                "resumo": resumo,
                "distancia_km": round(distancia, 1),
                "origem": {
                    "inquilino": alarme["inquilino"],
                    "site": alarme["site"],
                    "dominio": alarme["dominio"],
                    "regra": alarme["regra"],
                },
                "satelite": alarme["dados"].get("satelite"),
            }
            with self.engine.begin() as conexao:
                entrega_id = conexao.execute(
                    pg_insert(a)
                    .values(
                        inquilino_id=ass["inquilino_id"],
                        assinatura_id=ass["id"],
                        origem_alarme_id=uuid.UUID(alarme["id"]),
                        severidade=alarme["severidade"],
                        estado="ativo",
                        ocorrido_em=alarme["ocorrido_em"],
                        dados=jsonable_encoder(dados),
                    )
                    .on_conflict_do_nothing()
                    .returning(a.c.id)
                ).scalar()
            if entrega_id is None:
                continue
            entrega = self.carregar_alarme(entrega_id)
            log.warning("Alerta entregue a %s: %s", ass["assinante"], resumo)
            self._publicar_alarme("aberto", entrega)
            self._comandar_terminais(entrega, "exibir_alerta")

    def _comandar_terminais(self, alarme: dict, acao: str) -> None:
        d, i = db.dispositivo, db.inquilino
        with self.engine.connect() as conexao:
            terminais = conexao.execute(
                select(d.c.slug)
                .join(i, i.c.id == d.c.inquilino_id)
                .where(i.c.slug == alarme["inquilino"], d.c.tipo == "terminal", d.c.revogado_em.is_(None))
            ).scalars()
            terminais = list(terminais)
        dados = {
            "acao": acao,
            "severidade": alarme["severidade"],
            "alarme_id": alarme["id"],
            "resumo": alarme["dados"].get("resumo"),
            "origem": alarme["dados"].get("origem"),
        }
        for terminal in terminais:
            try:
                enviar_comando(self.engine, self.publicar, alarme["inquilino"], terminal, dados, alarme_id=alarme["id"])
            except FalhaAcao as exc:
                log.warning("Terminal %s: %s", terminal, exc)

    # ── Consultas ───────────────────────────────────────────────────────────

    def regras(self, inquilino: str, dominio: str) -> list[Regra]:
        chave = (inquilino, dominio)
        em_cache = self._cache.get(chave)
        if em_cache and time.monotonic() - em_cache[0] < CACHE_REGRAS_S:
            return em_cache[1]
        regras = carregar_regras(self.engine, inquilino, dominio)
        self._cache[chave] = (time.monotonic(), regras)
        return regras

    def _abertos(self, dispositivo_id: uuid.UUID) -> list[dict]:
        a = db.alarme
        with self.engine.connect() as conexao:
            linhas = conexao.execute(
                select(a.c.id, a.c.regra_id, a.c.severidade, a.c.dados).where(
                    a.c.dispositivo_id == dispositivo_id, a.c.estado != "resolvido", a.c.origem_alarme_id.is_(None)
                )
            ).all()
        return [
            {
                "id": str(x.id),
                "regra_id": str(x.regra_id) if x.regra_id else None,
                "severidade": x.severidade,
                "dados": x.dados or {},
            }
            for x in linhas
        ]

    def _satelite(self, origem: Origem) -> dict:
        """Correlação satélite × estação: última varredura FIRMS do mesmo site, se houver."""
        ue, d, s = db.ultimo_estado, db.dispositivo, db.site
        with self.engine.connect() as conexao:
            leituras = conexao.execute(
                select(ue.c.grandeza, ue.c.valor, ue.c.tempo)
                .join(d, d.c.id == ue.c.dispositivo_id)
                .join(s, s.c.id == d.c.site_id)
                .where(s.c.slug == origem.site, d.c.tipo == "conector", ue.c.grandeza.in_(["focos", "frp_max"]))
            ).all()
        if not leituras:
            return {}
        valores = {g: v for g, v, _ in leituras}
        return {
            "satelite": {
                "focos": int(valores.get("focos", 0)),
                "frp_max": valores.get("frp_max", 0),
                "confirmado": valores.get("focos", 0) > 0,
                "varredura_em": max(t for _, _, t in leituras),
            }
        }

    def carregar_alarme(self, alarme_id) -> dict | None:
        linha = consultar_alarmes(self.engine, ids=[uuid.UUID(str(alarme_id))])
        return linha[0] if linha else None

    def _publicar_alarme(self, evento: str, alarme: dict) -> None:
        topico = "/".join(
            (
                "plataforma/alarme",
                alarme["inquilino"],
                alarme["site"] or "entrega",
                alarme["dominio"] or "assinatura",
                alarme["dispositivo"] or alarme["id"][:8],
            )
        )
        self.publicar(topico, jsonable_encoder({"evento": evento, "alarme": alarme}))


# ──────────────────────────────────────────────
# Consultas compartilhadas com a API
# ──────────────────────────────────────────────


def carregar_regras(
    engine: Engine, inquilino: str, dominio: str | None = None, escopo: str | None = None
) -> list[Regra]:
    r, ra, dom, i = db.regra, db.regra_acao, db.dominio, db.inquilino
    consulta = (
        select(r.c.id, r.c.nome, r.c.severidade, r.c.condicao, r.c.janela_s, r.c.escopo)
        .join(dom, dom.c.id == r.c.dominio_id)
        .join(i, i.c.id == r.c.inquilino_id)
        .where(i.c.slug == inquilino, r.c.ativa)
        .order_by(r.c.nome)
    )
    if dominio:
        consulta = consulta.where(dom.c.slug == dominio)
    if escopo:
        consulta = consulta.where(r.c.escopo == escopo)
    with engine.connect() as conexao:
        linhas = conexao.execute(consulta).all()
        acoes: dict[uuid.UUID, list[dict]] = {}
        if linhas:
            for ac in conexao.execute(
                select(ra).where(ra.c.regra_id.in_([x.id for x in linhas])).order_by(ra.c.ordem)
            ).mappings():
                acoes.setdefault(ac["regra_id"], []).append({"id": str(ac["id"]), "tipo": ac["tipo"], **ac["config"]})
    return [
        Regra(x.nome, x.severidade, x.condicao, tuple(acoes.get(x.id, ())), x.janela_s, x.escopo, str(x.id))
        for x in linhas
    ]


def consultar_alarmes(engine: Engine, ids: list[uuid.UUID] | None = None, filtros: list | None = None, consulta=None):
    """Alarmes no formato do barramento/API (com slugs em vez de ids internos)."""
    with engine.connect() as conexao:
        q = consulta if consulta is not None else consulta_alarmes()
        if ids is not None:
            q = q.where(db.alarme.c.id.in_(ids))
        for f in filtros or []:
            q = q.where(f)
        return [formatar_alarme(dict(x)) for x in conexao.execute(q).mappings()]


def consulta_alarmes():
    a, i, d, s, p, dom, r = (
        db.alarme,
        db.inquilino,
        db.dispositivo,
        db.site,
        db.perfil_dispositivo,
        db.dominio,
        db.regra,
    )
    return (
        select(
            a.c.id,
            i.c.slug.label("inquilino"),
            a.c.severidade,
            a.c.estado,
            r.c.nome.label("regra"),
            d.c.slug.label("dispositivo"),
            s.c.slug.label("site"),
            s.c.nome.label("site_nome"),
            s.c.lat,
            s.c.lon,
            dom.c.slug.label("dominio"),
            dom.c.nome.label("dominio_nome"),
            a.c.ocorrido_em,
            a.c.reconhecido_em,
            a.c.reconhecido_por,
            a.c.resolvido_em,
            a.c.resolvido_por,
            a.c.ocorrencias,
            a.c.atualizado_em,
            a.c.origem_alarme_id,
            a.c.assinatura_id,
            a.c.dados,
        )
        .join(i, i.c.id == a.c.inquilino_id)
        .outerjoin(d, d.c.id == a.c.dispositivo_id)
        .outerjoin(s, s.c.id == d.c.site_id)
        .outerjoin(p, p.c.id == d.c.perfil_id)
        .outerjoin(dom, dom.c.id == p.c.dominio_id)
        .outerjoin(r, r.c.id == a.c.regra_id)
    )


def formatar_alarme(linha: dict) -> dict:
    linha["id"] = str(linha["id"])
    linha["origem_alarme_id"] = str(linha["origem_alarme_id"]) if linha["origem_alarme_id"] else None
    linha["assinatura_id"] = str(linha["assinatura_id"]) if linha["assinatura_id"] else None
    linha["dados"] = linha["dados"] or {}
    linha["regra"] = linha["regra"] or linha["dados"].get("regra_nome")
    return linha
