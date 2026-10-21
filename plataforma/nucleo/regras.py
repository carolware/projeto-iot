"""
regras.py — regras condição → ação (RF-PRO-02), as mesmas na central e nos
nós de borda (RF-EDG-03). Formato igual ao das tabelas `regra`/`regra_acao`:

    {
      "nome": "risco-critico",
      "severidade": "critico",              # medio | alto | critico
      "escopo": "borda",                    # borda: roda no nó mais perto do dispositivo
      "janela_s": 0,                        # > 0: a condição precisa durar a janela inteira
      "condicao": {"todas":  [{"grandeza": "fumaca", "op": ">=", "valor": 85}, ...]}
                | {"alguma": [...]}
                | {"ausencia": {"segundos": 300, "perfil": "estacao-incendio"}},
      "acoes": [{"tipo": "alarme"}, {"tipo": "comando", "acao": "acionar_brigada"}, ...]
    }

Escopo "borda" vale só para limiares instantâneos (sem janela e sem ausência):
o nó de borda decide com a leitura que acabou de chegar, sem histórico.
"""

import operator
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

OPERADORES = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
SEVERIDADES = ("medio", "alto", "critico")
ESCOPOS = ("central", "borda")
CANAIS = ("email", "sms", "whatsapp")

# campos obrigatórios da configuração de cada tipo de ação (Strategy na central)
TIPOS_ACAO: dict[str, tuple[str, ...]] = {
    "alarme": (),
    "comando": ("acao",),
    "webhook": ("url",),
    "publicar": ("topico",),
    "notificar": ("canal", "destino"),
}


@dataclass(frozen=True)
class Regra:
    nome: str
    severidade: str
    condicao: dict
    acoes: tuple[dict, ...] = field(default_factory=tuple)
    janela_s: int = 0
    escopo: str = "central"
    id: str | None = None

    @classmethod
    def de_dict(cls, bruto: dict) -> "Regra":
        erros = validar_regra(bruto)
        if erros:
            raise ValueError(f"regra {bruto.get('nome')!r} inválida: {'; '.join(erros)}")
        return cls(
            nome=bruto["nome"],
            severidade=bruto["severidade"],
            condicao=bruto["condicao"],
            acoes=tuple(bruto.get("acoes", ())),
            janela_s=int(bruto.get("janela_s", 0)),
            escopo=bruto.get("escopo", "central"),
            id=bruto.get("id"),
        )

    def para_dict(self) -> dict:
        saida = {
            "nome": self.nome,
            "severidade": self.severidade,
            "escopo": self.escopo,
            "janela_s": self.janela_s,
            "condicao": self.condicao,
            "acoes": list(self.acoes),
        }
        return saida | ({"id": self.id} if self.id else {})

    @property
    def de_ausencia(self) -> bool:
        return "ausencia" in self.condicao

    @property
    def gera_alarme(self) -> bool:
        return any(a.get("tipo") == "alarme" for a in self.acoes)

    def acoes_do_tipo(self, *tipos: str) -> list[dict]:
        return [a for a in self.acoes if a.get("tipo") in tipos]

    def grandezas(self) -> set[str]:
        if self.de_ausencia:
            return set()
        return {t["grandeza"] for t in next(iter(self.condicao.values()))}

    def atende(self, valores: dict[str, float]) -> bool:
        """Limiar instantâneo. Grandeza ausente torna o termo falso; regra de ausência nunca 'atende' aqui."""
        if self.de_ausencia:
            return False
        modo, termos = next(iter(self.condicao.items()))
        resultados = (
            t["grandeza"] in valores and OPERADORES[t["op"]](valores[t["grandeza"]], t["valor"]) for t in termos
        )
        return all(resultados) if modo == "todas" else any(resultados)


def validar_regra(bruto: object) -> list[str]:
    if not isinstance(bruto, dict):
        return ["regra precisa ser um objeto"]
    erros = []
    if not bruto.get("nome"):
        erros.append("nome obrigatório")
    if bruto.get("severidade") not in SEVERIDADES:
        erros.append(f"severidade deve ser uma de {SEVERIDADES}")
    escopo = bruto.get("escopo", "central")
    if escopo not in ESCOPOS:
        erros.append(f"escopo deve ser um de {ESCOPOS}")
    janela = bruto.get("janela_s", 0)
    if not isinstance(janela, int) or isinstance(janela, bool) or janela < 0:
        erros.append("janela_s deve ser um inteiro ≥ 0")
    erros += _validar_condicao(bruto.get("condicao"))
    erros += validar_acoes(bruto.get("acoes", []))
    if escopo == "borda" and (janela or (isinstance(bruto.get("condicao"), dict) and "ausencia" in bruto["condicao"])):
        erros.append("escopo borda aceita só limiares instantâneos (sem janela_s e sem ausência)")
    return erros


def _validar_condicao(condicao: object) -> list[str]:
    if (
        not isinstance(condicao, dict)
        or len(condicao) != 1
        or next(iter(condicao)) not in ("todas", "alguma", "ausencia")
    ):
        return ["condicao deve ter exatamente uma chave: 'todas', 'alguma' ou 'ausencia'"]
    modo, corpo = next(iter(condicao.items()))
    if modo == "ausencia":
        segundos = corpo.get("segundos") if isinstance(corpo, dict) else None
        if not isinstance(segundos, int) or isinstance(segundos, bool) or segundos <= 0:
            return ["ausencia.segundos deve ser um inteiro > 0"]
        return []
    if not isinstance(corpo, list) or not corpo:
        return ["condicao precisa de ao menos um termo"]
    erros = []
    for i, termo in enumerate(corpo):
        if not isinstance(termo, dict) or not isinstance(termo.get("grandeza"), str):
            erros.append(f"termo {i}: grandeza obrigatória")
        elif termo.get("op") not in OPERADORES:
            erros.append(f"termo {i}: op deve ser um de {sorted(OPERADORES)}")
        elif not isinstance(termo.get("valor"), int | float) or isinstance(termo.get("valor"), bool):
            erros.append(f"termo {i}: valor numérico obrigatório")
    return erros


def validar_acoes(acoes: object) -> list[str]:
    if not isinstance(acoes, list):
        return ["acoes deve ser uma lista"]
    erros = []
    for i, acao in enumerate(acoes):
        tipo = acao.get("tipo") if isinstance(acao, dict) else None
        if tipo not in TIPOS_ACAO:
            erros.append(f"acao {i}: tipo deve ser um de {sorted(TIPOS_ACAO)}")
            continue
        faltando = [c for c in TIPOS_ACAO[tipo] if not acao.get(c)]
        if faltando:
            erros.append(f"acao {i} ({tipo}): falta {', '.join(faltando)}")
        if tipo == "notificar" and acao.get("canal") not in CANAIS:
            erros.append(f"acao {i} (notificar): canal deve ser um de {CANAIS}")
        if tipo == "webhook" and not str(acao.get("url", "")).startswith(("http://", "https://")):
            erros.append(f"acao {i} (webhook): url deve começar com http:// ou https://")
    return erros


def mais_severa(regras: Iterable[Regra], valores: dict[str, float]) -> Regra | None:
    """A regra atendida de maior severidade (ou None)."""
    atendidas = [r for r in regras if r.atende(valores)]
    return max(atendidas, key=lambda r: SEVERIDADES.index(r.severidade), default=None)


def severidade_atinge(severidade: str, minima: str) -> bool:
    return SEVERIDADES.index(severidade) >= SEVERIDADES.index(minima)


def concorrem(a: Regra, b: Regra) -> bool:
    """Regras que olham para alguma grandeza em comum competem: só a mais severa vale (ex.: alto × crítico)."""
    return bool(a.grandezas() & b.grandezas())


def vencedoras(atendidas: list[Regra]) -> list[Regra]:
    """Das regras atendidas, as que não são superadas por outra mais severa que concorre com elas."""
    return [
        r
        for r in atendidas
        if not any(
            concorrem(r, outra) and SEVERIDADES.index(outra.severidade) > SEVERIDADES.index(r.severidade)
            for outra in atendidas
        )
    ]


def sustentada(
    regra: Regra, serie: list[tuple[datetime, dict[str, float]]], fim: datetime, tolerancia: timedelta
) -> bool:
    """
    Janela de tempo (ex.: "temperatura > 30 °C por 30 minutos"): toda leitura em
    [fim − janela, fim] atende à condição e a condição já valia no início da janela.

    "Já valia no início" = a última leitura até o início (inclusive) atende. Sem
    leitura anterior (dispositivo novo), a primeira da janela precisa estar a no
    máximo `tolerancia` do início. A série pode trazer leituras anteriores à janela.
    """
    inicio = fim - timedelta(seconds=regra.janela_s)
    pontos = sorted((t, v) for t, v in serie if t <= fim)
    dentro = [(t, v) for t, v in pontos if t >= inicio]
    if not dentro or not all(regra.atende(v) for _, v in dentro):
        return False
    ancora = [(t, v) for t, v in pontos if t <= inicio]
    if ancora and ancora[-1][0] >= inicio - tolerancia:
        return regra.atende(ancora[-1][1])
    return dentro[0][0] <= inicio + tolerancia
