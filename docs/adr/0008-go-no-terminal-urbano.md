# ADR 0008 — Go no terminal urbano

## Contexto
O critério da §9.3 pede integração demonstrada em pelo menos três linguagens,
usando só bibliotecas genéricas de MQTT/HTTP (RF-INT-02).

## Decisão
O terminal de alerta das prefeituras é escrito em **Go** (`paho.mqtt.golang`),
num contêiner próprio, usando só o contrato publicado. As outras linguagens são
Python (estações, atuadores, conector) e TypeScript (painel e exemplo do
[kit do integrador](../integrador/)).

## Alternativas
- **C/C++ (ESP32)**: realista para firmware, mas sem hardware (ADR 0011).
- **Rust**: viável, mas a equipe tem menos familiaridade.
- **Node.js no terminal**: já há TypeScript no painel; Go diversifica mais.

## Consequências
- Binário estático pequeno, adequado a um terminal embarcado.
- A derivação da credencial simulada é implementada nas duas linguagens, com
  vetor de teste cruzado.
