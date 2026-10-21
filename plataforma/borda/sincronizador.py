"""
sincronizador.py — esvazia a fila local para a central em lotes (RF-EDG-02).

Cada lote vai para POST /api/v1/ingestao/lote; só sai da fila o que a central
confirmou (aceita ou duplicada). Se o link cair no meio do envio, o lote
inteiro fica pendente e é reenviado depois: a central deduplica pelo
id_mensagem, então nada se perde e nada se repete (RNF-10).
"""

import logging
from collections.abc import Callable

from borda.fila import FilaLocal

log = logging.getLogger("borda.sync")


class LinkIndisponivel(Exception):
    """Falha de rede/HTTP ao falar com a central: tentar de novo mais tarde."""


class Sincronizador:
    def __init__(
        self,
        fila: FilaLocal,
        enviar_lote: Callable[[list[dict]], dict],
        tamanho_lote: int = 500,
    ):
        self.fila = fila
        self.enviar_lote = enviar_lote
        self.tamanho_lote = tamanho_lote

    def ciclo(self) -> int:
        """Envia um lote; devolve quantas mensagens saíram da fila. Levanta LinkIndisponivel."""
        itens = self.fila.proximas(self.tamanho_lote)
        if not itens:
            return 0
        resposta = self.enviar_lote([item.mensagem for item in itens])
        resultados = resposta["resultados"]
        if len(resultados) != len(itens):
            raise LinkIndisponivel(f"resposta com {len(resultados)} resultados para {len(itens)} mensagens")

        confirmadas, recusadas = [], {}
        for item, resultado in zip(itens, resultados, strict=True):
            if resultado["status"] in ("aceita", "duplicada"):
                confirmadas.append(item.id_mensagem)
            else:
                recusadas[item.id_mensagem] = "; ".join(f"{e['campo']}: {e['motivo']}" for e in resultado["erros"])
        self.fila.marcar_enviadas(confirmadas)
        self.fila.marcar_recusadas(recusadas)
        if recusadas:
            log.warning("Central recusou %d mensagem(ns): %s", len(recusadas), next(iter(recusadas.values())))
        return len(itens)

    def esvaziar(self) -> int:
        """Envia lotes até a fila ficar vazia (ou o link cair)."""
        total = 0
        while enviados := self.ciclo():
            total += enviados
        return total
