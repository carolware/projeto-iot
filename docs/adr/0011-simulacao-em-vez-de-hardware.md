# ADR 0011 — Dispositivos simulados em vez de hardware

## Contexto
O PDF põe o hardware fora do escopo (§1.2), e a equipe não tem sensores.

## Decisão
Todos os dispositivos são **simuladores em contêiner** que se comportam como
dispositivos reais: credencial própria, uma conexão MQTT/TLS cada, contrato
canônico. As estações usam dados reais de clima (Open-Meteo) e de focos de
calor (NASA FIRMS) com ruído, mais um modo de incêndio forçado para a
demonstração (`mock_incendio.py`).

## Alternativas
- **ESP32 com sensores reais**: custo, prazo e só alguns pontos.
- **Dados sintéticos puros**: perderiam a ligação com o Cerrado real.

## Consequências
- A demonstração é reproduzível em qualquer máquina com Docker.
- O nó de borda roda em contêiner; a referência de hardware (Raspberry Pi 5,
  RNF-06) não foi medida.
- O canal `simulacao/mock` existe só para a demonstração, com usuário próprio
  no broker.
