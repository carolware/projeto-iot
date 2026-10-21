"""
mqtt.py — adaptadores MQTT da central:

- entrada: assina telemetria/evento/estado de todos os inquilinos no broker
  central e entrega ao pipeline de ingestão;
- barramento interno (publicação/assinatura): republica as mensagens aceitas,
  já enriquecidas, em `plataforma/{tipo}/{inquilino}/{site}/{dominio}/{dispositivo}`
  e as recusadas em `plataforma/dlq`, para as próximas etapas (regras, tempo real).
"""

import json
import logging
import uuid

import paho.mqtt.client as mqtt

from nucleo.ingestao import MensagemEnriquecida, PipelineIngestao, Recusa
from nucleo.topicos import padrao_assinatura, parsear_topico

log = logging.getLogger("central.mqtt")

PREFIXO_BARRAMENTO = "plataforma"
TOPICO_DLQ = f"{PREFIXO_BARRAMENTO}/dlq"


def topico_barramento(m: MensagemEnriquecida) -> str:
    d = m.dispositivo
    return f"{PREFIXO_BARRAMENTO}/{m.mensagem.tipo}/{d.inquilino}/{d.site}/{d.dominio}/{d.slug}"


class BarramentoMqtt:
    def __init__(self, cliente: mqtt.Client):
        self.cliente = cliente

    def publicar_aceita(self, mensagem: MensagemEnriquecida) -> None:
        self.cliente.publish(topico_barramento(mensagem), json.dumps(mensagem.para_dict(), ensure_ascii=False), qos=1)

    def publicar_recusada(self, recusa: Recusa) -> None:
        carga = {
            "origem": recusa.origem,
            "erros": [{"campo": e.campo, "motivo": e.motivo} for e in recusa.erros],
            "payload": recusa.payload if isinstance(recusa.payload, dict | list | str) else repr(recusa.payload),
        }
        self.cliente.publish(TOPICO_DLQ, json.dumps(carga, ensure_ascii=False), qos=1)


class AdaptadorMqtt:
    TIPOS_INGERIDOS = ("telemetria", "evento", "estado")

    def __init__(self, broker: str, porta: int):
        self.broker = broker
        self.porta = porta
        self.cliente = mqtt.Client(client_id=f"central-{uuid.uuid4().hex[:10]}", clean_session=True)
        self.pipeline: PipelineIngestao | None = None
        self.cliente.on_connect = self._ao_conectar
        self.cliente.on_message = self._ao_receber

    def iniciar(self, pipeline: PipelineIngestao) -> None:
        self.pipeline = pipeline
        self.cliente.connect_async(self.broker, self.porta, keepalive=60)
        self.cliente.loop_start()

    def parar(self) -> None:
        self.cliente.loop_stop()
        self.cliente.disconnect()

    def _ao_conectar(self, cliente: mqtt.Client, userdata, flags, rc: int) -> None:
        if rc != 0:
            log.error("Falha ao conectar no broker %s:%d (rc=%d)", self.broker, self.porta, rc)
            return
        for tipo in self.TIPOS_INGERIDOS:
            cliente.subscribe(padrao_assinatura(tipo), qos=1)
        log.info("Ingestão MQTT ativa em %s:%d", self.broker, self.porta)

    def _ao_receber(self, cliente: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        topico = parsear_topico(msg.topic)
        if topico is None or self.pipeline is None:
            return
        try:
            bruto: object = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            bruto = msg.payload.decode(errors="replace")
        try:
            [resultado] = self.pipeline.processar([bruto], origem=f"mqtt:{msg.topic}", topico=topico)
        except Exception:
            log.exception("Erro ao processar mensagem de %s", msg.topic)
            return
        if resultado.status == "recusada":
            log.warning("Recusada %s: %s", msg.topic, "; ".join(str(e) for e in resultado.erros))
