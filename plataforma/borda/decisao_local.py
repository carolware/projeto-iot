"""
decisao_local.py — regras executadas no próprio nó de borda (RF-EDG-03): com o
link para a central caído, um risco crítico ainda aciona a brigada local em
poucos milissegundos. O alarme vira um evento que entra na fila com
prioridade máxima e chega à central com o horário em que ocorreu (RF-PRO-05).

Mantém o comportamento do gateway da 1ª entrega: dispara uma vez ao escalar
para crítico e só repete depois do cooldown.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from nucleo.mensagem import nova_mensagem, parsear_timestamp
from nucleo.regras import Regra, mais_severa
from nucleo.topicos import Topico, montar_topico


@dataclass(frozen=True)
class Decisao:
    regra: Regra
    alarme: dict  # evento canônico "alarme_local" (vai para a fila → central)
    comandos: list[tuple[str, dict]] = field(default_factory=list)  # (tópico local, comando canônico)


@dataclass
class _EstadoSite:
    severidade: str | None = None
    ultimo_alerta: float = 0.0


class DecisorLocal:
    def __init__(
        self,
        regras: list[Regra],
        borda_id: str,
        atuador_do_site: str = "atuador-{site}-01",
        cooldown_s: float = 30,
        atraso_max: timedelta = timedelta(minutes=5),
        relogio: Callable[[], float] = time.time,
    ):
        self.regras = regras
        self.borda_id = borda_id
        self.atuador_do_site = atuador_do_site
        self.cooldown_s = cooldown_s
        self.atraso_max = atraso_max
        self.relogio = relogio
        self._sites: dict[str, _EstadoSite] = {}

    def avaliar(self, mensagem: dict, topico: Topico) -> Decisao | None:
        if mensagem["tipo"] != "telemetria":
            return None
        agora = self.relogio()
        medido_em = parsear_timestamp(mensagem["timestamp"])
        # dado atrasado (ex.: reenviado depois de horas) não gera alarme como se fosse atual (RF-ING-04)
        if agora - medido_em.timestamp() > self.atraso_max.total_seconds():
            return None

        valores = {m["grandeza"]: m["valor"] for m in mensagem["medidas"]}
        regra = mais_severa(self.regras, valores)
        estado = self._sites.setdefault(topico.site, _EstadoSite())
        severidade = regra.severidade if regra else None
        escalou_para_critico = severidade == "critico" and estado.severidade != "critico"
        cooldown_expirou = agora - estado.ultimo_alerta >= self.cooldown_s
        estado.severidade = severidade

        if regra is None or not regra.acoes or not (escalou_para_critico or cooldown_expirou):
            return None
        estado.ultimo_alerta = agora
        return self._decidir(regra, mensagem, topico, valores)

    def _decidir(self, regra: Regra, mensagem: dict, topico: Topico, valores: dict) -> Decisao:
        atuador = self.atuador_do_site.format(site=topico.site)
        comandos = []
        for acao in regra.acoes:
            if acao.get("tipo") != "comando":
                continue
            comando = nova_mensagem(
                atuador,
                "comando",
                dados={
                    "acao": acao["acao"],
                    "severidade": regra.severidade,
                    "regra": regra.nome,
                    "origem": f"borda:{self.borda_id}",
                    "dispositivo_origem": mensagem["dispositivo"],
                    "leituras": valores,
                },
            )
            topico_cmd = montar_topico(topico.inquilino, topico.site, topico.dominio, atuador, "comando")
            comandos.append((topico_cmd, comando))

        alarme = nova_mensagem(
            mensagem["dispositivo"],
            "evento",
            timestamp=parsear_timestamp(mensagem["timestamp"]),
            via=self.borda_id,
            dados={
                "evento": "alarme_local",
                "regra": regra.nome,
                "severidade": regra.severidade,
                "leituras": valores,
                "mensagem_origem": mensagem["id_mensagem"],
                "comandos": [c["id_mensagem"] for _, c in comandos],
            },
        )
        return Decisao(regra, alarme, comandos)
