"""
regras.py — avaliação de regras condição → ação (RF-PRO-02), a mesma na
central e nos nós de borda (RF-EDG-03).

A condição tem o mesmo formato do campo `condicao` da tabela `regra`:

    {"todas":  [{"grandeza": "fumaca", "op": ">=", "valor": 85},
                {"grandeza": "umidade", "op": "<", "valor": 15}]}
    {"alguma": [...]}

Esta versão cobre limiares combinados; janela de tempo e ausência de dados
entram na etapa A3, junto com o motor da central.
"""

import operator
from collections.abc import Iterable
from dataclasses import dataclass, field

OPERADORES = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
SEVERIDADES = ("medio", "alto", "critico")


@dataclass(frozen=True)
class Regra:
    nome: str
    severidade: str
    condicao: dict
    acoes: tuple[dict, ...] = field(default_factory=tuple)

    @classmethod
    def de_dict(cls, bruto: dict) -> "Regra":
        erros = validar_regra(bruto)
        if erros:
            raise ValueError(f"regra {bruto.get('nome')!r} inválida: {'; '.join(erros)}")
        return cls(bruto["nome"], bruto["severidade"], bruto["condicao"], tuple(bruto.get("acoes", ())))

    def atende(self, valores: dict[str, float]) -> bool:
        """Grandeza ausente nas leituras torna o termo falso (não há como afirmar a condição)."""
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
    condicao = bruto.get("condicao")
    if not isinstance(condicao, dict) or len(condicao) != 1 or next(iter(condicao)) not in ("todas", "alguma"):
        return [*erros, "condicao deve ter exatamente uma chave: 'todas' ou 'alguma'"]
    termos = next(iter(condicao.values()))
    if not isinstance(termos, list) or not termos:
        return [*erros, "condicao precisa de ao menos um termo"]
    for i, termo in enumerate(termos):
        if not isinstance(termo, dict) or not isinstance(termo.get("grandeza"), str):
            erros.append(f"termo {i}: grandeza obrigatória")
        elif termo.get("op") not in OPERADORES:
            erros.append(f"termo {i}: op deve ser um de {sorted(OPERADORES)}")
        elif not isinstance(termo.get("valor"), int | float) or isinstance(termo.get("valor"), bool):
            erros.append(f"termo {i}: valor numérico obrigatório")
    return erros


def mais_severa(regras: Iterable[Regra], valores: dict[str, float]) -> Regra | None:
    """A regra atendida de maior severidade (ou None)."""
    atendidas = [r for r in regras if r.atende(valores)]
    return max(atendidas, key=lambda r: SEVERIDADES.index(r.severidade), default=None)
