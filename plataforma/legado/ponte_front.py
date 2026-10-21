"""
ponte_front.py — adaptador TEMPORÁRIO entre o barramento da plataforma e o
painel da 1ª entrega. Sai na etapa A5, quando o front passa a consumir a API
e o tempo real da central.

  plataforma/telemetria/brigada-cerrado/...  ──►  {TOPIC_BASE}/{zona}/sensor/{grandeza}
  plataforma/evento/... varredura_firms      ──►  {TOPIC_BASE}/{zona}/satelite/firms   (retido)
  plataforma/evento/... alarme_local         ──►  {TOPIC_BASE}/{zona}/atuador/alerta
  {TOPIC_BASE}/{zona}/atuador/alerta (painel) ──►  comando canônico ao atuador da zona
                                                   (desce para o campo pela bridge da borda)
"""

import json
import logging
import os
import uuid

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from nucleo.mensagem import nova_mensagem
from nucleo.topicos import montar_topico

load_dotenv()

BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_BASE = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/monitoramento-br")
INQUILINO = "brigada-cerrado"
ACOES_PAINEL = {"acionar_brigada", "reforcar_monitoramento", "silenciar_alerta"}

# o front antigo usa "pirinopolis"; o cadastro corrigiu para "pirenopolis"
SITE_PARA_FRONT = {"pirenopolis": "pirinopolis"}
FRONT_PARA_SITE = {v: k for k, v in SITE_PARA_FRONT.items()}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ponte]   %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ponte-front")

firms_confirmado: dict[str, bool] = {}


def _publicar(cliente: mqtt.Client, zona: str, sufixo: str, carga: dict, retain: bool = False) -> None:
    cliente.publish(f"{TOPIC_BASE}/{zona}/{sufixo}", json.dumps(carga, ensure_ascii=False), qos=0, retain=retain)


def telemetria(cliente: mqtt.Client, msg: dict) -> None:
    if not msg["dispositivo"].startswith("est-"):
        return
    zona = SITE_PARA_FRONT.get(msg["contexto"]["site"], msg["contexto"]["site"])
    for medida in msg["medidas"]:
        _publicar(
            cliente,
            zona,
            f"sensor/{medida['grandeza']}",
            {"valor": medida["valor"], "timestamp": msg["timestamp"], "fonte": msg.get("fonte")},
        )
    geo = (msg.get("local") or {}).get("geo")
    if geo:
        lon, lat = geo["coordinates"]
        _publicar(cliente, zona, "sensor/lat", {"valor": lat, "timestamp": msg["timestamp"]})
        _publicar(cliente, zona, "sensor/lon", {"valor": lon, "timestamp": msg["timestamp"]})


def evento(cliente: mqtt.Client, msg: dict) -> None:
    site = msg["contexto"]["site"]
    zona = SITE_PARA_FRONT.get(site, site)
    dados = msg["dados"]
    if dados.get("evento") == "varredura_firms":
        firms_confirmado[site] = bool(dados.get("confirmado"))
        carga = {k: v for k, v in dados.items() if k != "evento"}
        _publicar(
            cliente, zona, "satelite/firms", {"zona": zona, "atualizado_em": msg["timestamp"], **carga}, retain=True
        )
    elif dados.get("evento") == "alarme_local":
        leituras = dados.get("leituras", {})
        _publicar(
            cliente,
            zona,
            "atuador/alerta",
            {
                "zona": zona,
                "risco": dados["severidade"],
                "temperatura": leituras.get("temperatura"),
                "umidade": leituras.get("umidade"),
                "fumaca": leituras.get("fumaca"),
                "timestamp": msg["timestamp"],
                "firms_confirmado": firms_confirmado.get(site, False),
                "acao": "acionar_brigada" if dados["severidade"] == "critico" else "reforcar_monitoramento",
                "origem": f"borda:{msg.get('via')}",
            },
        )


def comando_do_painel(cliente: mqtt.Client, topico: str, carga: dict) -> None:
    if carga.get("origem") != "dashboard" or carga.get("acao") not in ACOES_PAINEL:
        return
    site = FRONT_PARA_SITE.get(topico.split("/")[-3], topico.split("/")[-3])
    atuador = f"atuador-{site}-01"
    comando = nova_mensagem(
        atuador,
        "comando",
        dados={
            "acao": carga["acao"],
            "severidade": carga.get("risco", "alto"),
            "origem": "painel",
        },
    )
    cliente.publish(montar_topico(INQUILINO, site, "incendio", atuador, "comando"), json.dumps(comando), qos=1)
    log.info("Comando do painel → %s: %s", atuador, carga["acao"])


def ao_receber(cliente: mqtt.Client, userdata, mensagem: mqtt.MQTTMessage) -> None:
    try:
        carga = json.loads(mensagem.payload)
        if mensagem.topic.startswith("plataforma/telemetria/"):
            telemetria(cliente, carga)
        elif mensagem.topic.startswith("plataforma/evento/"):
            evento(cliente, carga)
        else:
            comando_do_painel(cliente, mensagem.topic, carga)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log.warning("Ignorada %s: %s", mensagem.topic, exc)


def main() -> None:
    cliente = mqtt.Client(client_id=f"ponte-front-{uuid.uuid4().hex[:8]}")
    cliente.on_message = ao_receber
    cliente.on_connect = lambda c, u, f, rc: c.subscribe(
        [
            (f"plataforma/telemetria/{INQUILINO}/#", 0),
            (f"plataforma/evento/{INQUILINO}/#", 0),
            (f"{TOPIC_BASE}/+/atuador/alerta", 1),
        ]
    )
    cliente.connect(BROKER, PORT, keepalive=60)
    log.info("Ponte temporária plataforma → painel antigo ativa em %s:%d", BROKER, PORT)
    cliente.loop_forever()


if __name__ == "__main__":
    main()
