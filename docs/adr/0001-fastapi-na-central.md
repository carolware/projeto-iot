# ADR 0001 — Python + FastAPI na central e na borda

## Contexto
A 1ª entrega era um conjunto de scripts Python (sensor, gateway, atuador). A
equipe domina Python, e a central precisa de API REST documentada em OpenAPI 3
(RF-APP-01), WebSocket (RF-APP-02) e o mesmo domínio rodando na borda (§7.1).

## Decisão
Central em **FastAPI** (uvicorn), SQLAlchemy Core e Alembic. O domínio fica num
pacote Python puro (`plataforma/nucleo/`), usado pela central e pela borda.

## Alternativas
- **Node.js/NestJS**: bom em I/O, mas reescreveria a lógica da 1ª entrega e
  duplicaria o domínio entre linguagens.
- **Java/Spring**: maduro, mas pesado para o nó de borda (Raspberry Pi, RNF-06)
  e fora do domínio da equipe.
- **Django**: ORM e admin prontos, mas o OpenAPI e o WebSocket são menos diretos.

## Consequências
- OpenAPI e Swagger gerados a partir dos modelos Pydantic, testados no CI.
- Um processo Python usa um núcleo de CPU (GIL). Isso limitou o teste de carga
  e levou ao [ADR 0009](0009-ingestao-mqtt-separada.md).
- A borda roda a mesma imagem Docker da central (prática da §7.3).
