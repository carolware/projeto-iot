"""
Dispositivo de teste em Python: só bibliotecas genéricas (paho-mqtt e requests).

    pip install paho-mqtt==1.6.1 requests
    SLUG=sensor-teste-01 CREDENCIAL=... python dispositivo.py
"""

import json
import os
import ssl
import time
import uuid
from datetime import UTC, datetime

import paho.mqtt.client as mqtt
import requests

SLUG = os.environ["SLUG"]
CREDENCIAL = os.environ["CREDENCIAL"]
INQUILINO = os.getenv("INQUILINO", "brigada-cerrado")
SITE = os.getenv("SITE", "mirador")
DOMINIO = os.getenv("DOMINIO", "incendio")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_CA = os.getenv("MQTT_CA", "plataforma/infra/pki/gerado/ca.crt")
API = os.getenv("API", "http://localhost:8000")

BASE = f"{INQUILINO}/{SITE}/{DOMINIO}/{SLUG}"


def mensagem(temperatura: float) -> dict:
    """Mensagem canônica v1.0 (contratos/mensagem-canonica.v1.schema.json)."""
    return {
        "versao": "1.0",
        "id_mensagem": str(uuid.uuid4()),  # gerado na origem: reenviar o mesmo id não duplica
        "dispositivo": SLUG,
        "tipo": "telemetria",
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "medidas": [
            {"grandeza": "temperatura", "valor": temperatura, "unidade": "Cel"},
            {"grandeza": "umidade", "valor": 40.0, "unidade": "%"},
            {"grandeza": "fumaca", "valor": 5.0, "unidade": "1"},
        ],
    }


def ao_conectar(cliente, userdata, flags, rc):
    if rc != 0:  # 5 = não autorizado: credencial errada ou revogada
        print(f"MQTT: conexão recusada pelo broker (rc={rc}: {mqtt.connack_string(rc)})", flush=True)
        os._exit(1)
    print("MQTT conectado")
    cliente.subscribe(f"{BASE}/comando", qos=1)


def ao_receber(cliente, userdata, msg):
    comando = json.loads(msg.payload)
    print("comando recebido:", comando["dados"])
    resposta = {
        **mensagem(0),
        "tipo": "evento",
        "dados": {"evento": "executado", "comando_id": comando["id_mensagem"]},
    }
    del resposta["medidas"]
    cliente.publish(f"{BASE}/evento", json.dumps(resposta), qos=1)


cliente = mqtt.Client(client_id=SLUG)
cliente.username_pw_set(SLUG, CREDENCIAL)
cliente.tls_set(ca_certs=MQTT_CA, tls_version=ssl.PROTOCOL_TLS_CLIENT)
cliente.on_connect = ao_conectar
cliente.on_message = ao_receber
cliente.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
cliente.loop_start()
time.sleep(1)

for temperatura in (24.0, 25.5, 26.1):
    cliente.publish(f"{BASE}/telemetria", json.dumps(mensagem(temperatura)), qos=1).wait_for_publish()
    print(f"MQTT: telemetria publicada ({temperatura} °C)")

resposta = requests.post(f"{API}/api/v1/ingestao", json=mensagem(27.0), auth=(SLUG, CREDENCIAL), timeout=10)
print(f"HTTP: {resposta.status_code} {resposta.json()}")

print("aguardando comandos por 15 s…")
time.sleep(15)
cliente.loop_stop()
cliente.disconnect()
