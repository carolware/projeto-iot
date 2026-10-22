# Registro de decisões de arquitetura (ADR)

Cada decisão tem contexto, alternativas consideradas e consequências (§7 do
documento de requisitos). Status de todas: **aceita** (2026-10).

| ADR | Decisão |
|---|---|
| [0001](0001-fastapi-na-central.md) | Python + FastAPI na instância central e na borda |
| [0002](0002-mosquitto-broker-e-barramento.md) | Mosquitto como broker MQTT e como barramento interno |
| [0003](0003-postgres-timescaledb.md) | PostgreSQL + TimescaleDB como banco único |
| [0004](0004-fila-sqlite-cifrada-na-borda.md) | Fila SQLite cifrada (Fernet) no nó de borda |
| [0005](0005-borda-sobe-por-lote-http.md) | Borda sobe dados por lote HTTP e recebe comandos pela bridge MQTT |
| [0006](0006-keycloak.md) | Keycloak para identidade, papéis e 2FA |
| [0007](0007-credenciais-e-acl-gerados.md) | Senhas e ACL do Mosquitto geradas a partir do cadastro |
| [0008](0008-go-no-terminal-urbano.md) | Go no terminal urbano (terceira linguagem) |
| [0009](0009-ingestao-mqtt-separada.md) | Ingestão MQTT em serviço separado da API |
| [0010](0010-assinaturas-entre-inquilinos.md) | Cenário urbano por assinaturas de alerta entre inquilinos |
| [0011](0011-simulacao-em-vez-de-hardware.md) | Dispositivos simulados em vez de hardware |
