"""
mqtt.py — adaptadores MQTT da central:

- entrada: assina telemetria/evento/estado de todos os inquilinos no broker
  central e entrega ao pipeline de ingestão em micro-lotes: o callback do
  paho só enfileira, e uma thread grava tudo o que se acumulou numa única
  transação (com pouca carga o lote é de 1 e não há espera; sob carga, o custo
  por mensagem cai de ~25 ms para frações de ms — RNF-01);
- barramento interno (publicação/assinatura): republica as mensagens aceitas,
  já enriquecidas, em `plataforma/{tipo}/{inquilino}/{site}/{dominio}/{dispositivo}`
  e as recusadas em `plataforma/dlq`, para as próximas etapas (regras, tempo real).
"""

import json
import logging
import queue
import threading
import uuid

import paho.mqtt.client as mqtt

from central.metricas import LOTE_MQTT
from nucleo.conexao_mqtt import configurar
from nucleo.ingestao import Barramento, Entrada, MensagemEnriquecida, PipelineIngestao, Recusa
from nucleo.topicos import padrao_assinatura, parsear_topico

log = logging.getLogger("central.mqtt")

PREFIXO_BARRAMENTO = "plataforma"
TOPICO_DLQ = f"{PREFIXO_BARRAMENTO}/dlq"


def topico_barramento(m: MensagemEnriquecida) -> str:
    d = m.dispositivo
    return f"{PREFIXO_BARRAMENTO}/{m.mensagem.tipo}/{d.inquilino}/{d.site}/{d.dominio}/{d.slug}"


class BarramentoMqtt:
    def __init__(self, cliente: mqtt.Client, instancia: str | None = None):
        self.cliente = cliente
        self.instancia = instancia  # quem ingeriu: quem ouve o barramento ignora o que ela mesma publicou

    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None:
        carga = mensagem.para_dict()
        if self.instancia:
            carga["contexto"]["instancia"] = self.instancia
        # QoS 0: é um fan-out informativo (o registro durável já está no banco) e, no paho 1.x,
        # cada PUBACK percorre todas as mensagens em voo — com QoS 1 a 1.000 msg/s isso trava a ingestão
        self.cliente.publish(topico_barramento(mensagem), json.dumps(carga, ensure_ascii=False), qos=0)

    def publicar_recusada(self, recusa: Recusa) -> None:
        carga = {
            "origem": recusa.origem,
            "erros": [{"campo": e.campo, "motivo": e.motivo} for e in recusa.erros],
            "payload": recusa.payload if isinstance(recusa.payload, dict | list | str) else repr(recusa.payload),
        }
        self.cliente.publish(TOPICO_DLQ, json.dumps(carga, ensure_ascii=False), qos=1)


class AdaptadorMqtt:
    TIPOS_INGERIDOS = ("telemetria", "evento", "estado")
    LOTE_MAXIMO = 1000

    def __init__(
        self,
        broker: str,
        porta: int,
        client_id: str | None = None,
        inquilinos: list[str] | None = None,
        ingerir: bool = True,
        grupo: str | None = None,
    ):
        self.broker = broker
        self.porta = porta
        # None = todos; uma lista permite repartir os inquilinos entre réplicas da central (RNF-09)
        self.inquilinos = inquilinos or ["+"]
        # False: só publica e ouve o barramento (a ingestão roda no serviço central-ingestao)
        self.ingerir = ingerir
        # assinatura compartilhada ($share/grupo/...): réplicas do mesmo grupo dividem as mensagens
        self.prefixo = f"$share/{grupo}/" if grupo else ""
        self._barramento: list[str] = []
        # client_id estável + sessão persistente: o broker guarda as mensagens QoS 1 que
        # chegarem enquanto a central reinicia, e elas são ingeridas na volta (RNF-10)
        persistente = client_id is not None
        self.cliente = configurar(
            mqtt.Client(client_id=client_id or f"central-{uuid.uuid4().hex[:10]}", clean_session=not persistente)
        )
        self.pipeline: PipelineIngestao | None = None
        self._fila: queue.Queue[Entrada | None] = queue.Queue()
        self._gravador = threading.Thread(target=self._gravar, name="ingestao-mqtt", daemon=True)
        self.cliente.on_connect = self._ao_conectar
        self.cliente.on_message = self._ao_receber

    def iniciar(self, pipeline: PipelineIngestao | None = None) -> None:
        self.pipeline = pipeline
        if self.ingerir:
            self._gravador.start()
        self.cliente.connect_async(self.broker, self.porta, keepalive=60)
        self.cliente.loop_start()

    def parar(self) -> None:
        self.cliente.loop_stop()
        self.cliente.disconnect()
        self._fila.put(None)

    def _ao_conectar(self, cliente: mqtt.Client, userdata, flags, rc: int) -> None:
        if rc != 0:
            log.error("Falha ao conectar no broker %s:%d (rc=%d)", self.broker, self.porta, rc)
            return
        if self.ingerir:
            for tipo in self.TIPOS_INGERIDOS:
                for inquilino in self.inquilinos:
                    cliente.subscribe(self.prefixo + padrao_assinatura(tipo, inquilino), qos=1)
            log.info("Ingestão MQTT ativa em %s:%d", self.broker, self.porta)
        for padrao in self._barramento:
            cliente.subscribe(padrao, qos=0)

    def ouvir_barramento(self, destino: Barramento, ignorar_instancia: str) -> None:
        """Entrega a `destino` (motor, tempo real) o que outras instâncias ingeriram e publicaram."""
        self._destino, self._ignorar = destino, ignorar_instancia
        for tipo in self.TIPOS_INGERIDOS:
            padrao = f"{PREFIXO_BARRAMENTO}/{tipo}/#"
            self._barramento.append(padrao)
            self.cliente.message_callback_add(padrao, self._ao_receber_do_barramento)

    def _ao_receber_do_barramento(self, cliente, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            carga = json.loads(msg.payload)
            if carga.get("contexto", {}).get("instancia") == self._ignorar:
                return  # já entregue em processo
            self._destino.publicar_aceita(MensagemEnriquecida.de_dict(carga))
        except Exception:
            log.exception("Mensagem inválida no barramento (%s)", msg.topic)

    def _ao_receber(self, cliente: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        topico = parsear_topico(msg.topic)
        if topico is None:
            return
        try:
            bruto: object = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            bruto = msg.payload.decode(errors="replace")
        self._fila.put(Entrada(bruto, f"mqtt:{msg.topic}", topico))

    def _gravar(self) -> None:
        """Esvazia a fila em lotes: espera a primeira mensagem e leva junto o que já estiver esperando."""
        while (primeira := self._fila.get()) is not None:
            lote = [primeira]
            while len(lote) < self.LOTE_MAXIMO:
                try:
                    proxima = self._fila.get_nowait()
                except queue.Empty:
                    break
                if proxima is None:
                    self._fila.put(None)
                    break
                lote.append(proxima)
            LOTE_MQTT.observe(len(lote))
            try:
                self.pipeline.processar_entradas(lote)  # type: ignore[union-attr]
            except Exception:
                log.exception("Erro ao processar lote MQTT de %d mensagens", len(lote))
