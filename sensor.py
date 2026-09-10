import json
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.getenv("MQTT_PORT", "1883"))
CLIENT_ID = "sensor-cidade-incendio"

TOPIC_PREFIX = os.getenv("MQTT_TOPIC_BASE", "sentinela-iot-2026-joao-carol/chapada-veadeiros")

ZONAS = ["alto-paraiso", "vila-sao-jorge", "cavalcante", "colinas-do-sul"]

# estado inicial de cada zona
estado = {
    zona: {"temperatura": 25.0, "umidade": 55.0, "fumaca": 5.0}
    for zona in ZONAS
}


def atualizar_sensor(zona: str, forcar_foco: bool = False) -> dict:
    s = estado[zona]

    s["temperatura"] += random.uniform(-1, 1)
    s["umidade"] += random.uniform(-2, 2)
    s["fumaca"] += random.uniform(-1, 1)

    chance_de_foco = forcar_foco or random.random() < 0.02  # ~2% por leitura
    if chance_de_foco:
        s["fumaca"] += random.uniform(20, 40)
        s["temperatura"] += random.uniform(5, 15)
        s["umidade"] -= random.uniform(10, 20)

    s["fumaca"] = max(0.0, min(100.0, s["fumaca"]))
    s["umidade"] = max(0.0, min(100.0, s["umidade"]))
    s["temperatura"] = max(15.0, s["temperatura"])

    return {
        "zona": zona,
        "temperatura": round(s["temperatura"], 1),
        "umidade": round(s["umidade"], 1),
        "fumaca": round(s["fumaca"], 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def publicar_leitura(client: mqtt.Client, leitura: dict) -> None:
    """publica a leitura de uma zona nos tópicos separados por tipo de dado."""
    zona = leitura["zona"]

    client.publish(
        f"{TOPIC_PREFIX}/{zona}/sensor/temperatura",
        json.dumps({"valor": leitura["temperatura"], "timestamp": leitura["timestamp"]}),
    )
    client.publish(
        f"{TOPIC_PREFIX}/{zona}/sensor/umidade",
        json.dumps({"valor": leitura["umidade"], "timestamp": leitura["timestamp"]}),
    )
    client.publish(
        f"{TOPIC_PREFIX}/{zona}/sensor/fumaca",
        json.dumps({"valor": leitura["fumaca"], "timestamp": leitura["timestamp"]}),
    )

    print(
        f"[sensor] {zona:20s} "
        f"temp={leitura['temperatura']:5.1f}c  "
        f"umid={leitura['umidade']:5.1f}%  "
        f"fumaca={leitura['fumaca']:5.1f}%"
    )


def main() -> None:
    client = mqtt.Client(client_id=CLIENT_ID)
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_start()

    print(f"conectado ao broker {BROKER}, publicando em '{TOPIC_PREFIX}/*'")
    print("ctrl+c para parar\n")

    ciclo = 0
    try:
        while True:
            for zona in ZONAS:
                forcar = ciclo > 0 and ciclo % 40 == 0 and zona == random.choice(ZONAS)
                leitura = atualizar_sensor(zona, forcar_foco=forcar)
                publicar_leitura(client, leitura)

            ciclo += 1
            time.sleep(3)  # nova leitura a cada 3 segundos
    except KeyboardInterrupt:
        print("\nencerrando sensor...")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()