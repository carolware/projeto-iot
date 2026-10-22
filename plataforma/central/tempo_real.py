"""
tempo_real.py — assinatura de dados em tempo real por WebSocket (RF-APP-02).

O hub recebe, pelo barramento interno, cada mensagem aceita pela ingestão e
cada mudança de alarme do motor de regras, e entrega aos navegadores
conectados — cada um só recebe o que é do próprio inquilino (RF-SEG-03).

As publicações chegam de threads (MQTT, motor, rotas síncronas); a entrega
acontece no loop asyncio de cada conexão, por call_soon_threadsafe.
"""

import asyncio
import logging
from collections import defaultdict

from fastapi.encoders import jsonable_encoder

from nucleo.ingestao import MensagemEnriquecida, Recusa

log = logging.getLogger("central.tempo_real")

LIMITE_FILA = 5000  # ~5 s a 1.000 msg/s; navegador lento demais perde as mensagens mais antigas, não trava o servidor


class HubTempoReal:
    def __init__(self):
        # inquilino → {fila: loop da conexão que a consome}
        self._assinantes: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = defaultdict(dict)

    def assinar(self, inquilino: str) -> asyncio.Queue:
        """Chamar de dentro da corrotina da conexão (registra o loop dela)."""
        fila: asyncio.Queue = asyncio.Queue(maxsize=LIMITE_FILA)
        self._assinantes[inquilino][fila] = asyncio.get_running_loop()
        return fila

    def cancelar(self, inquilino: str, fila: asyncio.Queue) -> None:
        self._assinantes[inquilino].pop(fila, None)

    def conexoes(self) -> int:
        return sum(len(filas) for filas in self._assinantes.values())

    def publicar(self, inquilino: str, carga: dict, serializavel: bool = False) -> None:
        """Seguro para chamar de qualquer thread. `serializavel`: a carga já é JSON puro (pula a conversão)."""
        assinantes = list(self._assinantes.get(inquilino, {}).items())
        if not assinantes:
            return
        if not serializavel:
            carga = jsonable_encoder(carga)
        for fila, loop in assinantes:
            try:
                loop.call_soon_threadsafe(_entregar, fila, carga)
            except RuntimeError:  # loop já encerrado: conexão morreu sem cancelar
                self.cancelar(inquilino, fila)

    # ── Barramento (Observer): mensagens aceitas pela ingestão ──────────────

    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None:
        self.publicar(
            mensagem.dispositivo.inquilino,
            {"tipo": mensagem.mensagem.tipo, "mensagem": mensagem.para_dict()},
            serializavel=True,  # para_dict() já devolve só str/números/listas/dicts
        )

    def publicar_recusada(self, recusa: Recusa) -> None:
        pass

    # ── Alarmes: o motor publica em plataforma/alarme/{inquilino}/... ───────

    def de_topico(self, topico: str, carga: dict) -> None:
        partes = topico.split("/")
        if len(partes) >= 3 and partes[0] == "plataforma" and partes[1] == "alarme":
            self.publicar(partes[2], {"tipo": "alarme", **carga})


def _entregar(fila: asyncio.Queue, carga: dict) -> None:
    if fila.full():
        fila.get_nowait()
    fila.put_nowait(carga)
