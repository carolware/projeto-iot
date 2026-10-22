"""
conexao_mqtt.py — configuração única de TLS e autenticação para todos os
clientes MQTT da plataforma (central, borda, simuladores, ponte).

Variáveis de ambiente:
  MQTT_BROKER, MQTT_PORT   endereço (TLS em 8883)
  MQTT_CA                  certificado da CA própria (vazio = sem TLS, só para testes)
  MQTT_USUARIO / MQTT_SENHA  credencial do serviço (dispositivos usam a própria)
"""

import os
import ssl

import paho.mqtt.client as mqtt


def configurar(cliente: mqtt.Client, usuario: str | None = None, senha: str | None = None) -> mqtt.Client:
    """Aplica usuário/senha e TLS 1.2+ com verificação do certificado do broker."""
    usuario = usuario or os.getenv("MQTT_USUARIO")
    senha = senha or os.getenv("MQTT_SENHA")
    if usuario:
        cliente.username_pw_set(usuario, senha)
    ca = os.getenv("MQTT_CA", "")
    if ca:
        cliente.tls_set(ca_certs=ca, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    return cliente


def endereco() -> tuple[str, int]:
    return os.getenv("MQTT_BROKER", "localhost"), int(os.getenv("MQTT_PORT", "8883"))
