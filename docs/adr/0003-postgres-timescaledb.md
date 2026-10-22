# ADR 0003 — PostgreSQL + TimescaleDB como banco único

## Contexto
A plataforma guarda catálogo relacional (inquilinos, sites, perfis, regras,
alarmes) e séries temporais com consulta por intervalo (RF-DAD-01), último
estado (RF-DAD-02) e agregados por hora (RF-PRO-04).

## Decisão
Um único **PostgreSQL 16 com TimescaleDB**: hypertables para `telemetria` e
`evento`, agregado contínuo `telemetria_1h`, compressão após 7 dias; tabelas
comuns para o resto. Modelo em [dados.md](../dados.md).

## Alternativas
- **InfluxDB + Postgres**: dois bancos, sem join entre série e cadastro, e a
  deduplicação por `id_mensagem` ficaria fora da transação.
- **MongoDB**: flexível para JSON, mas fraco em séries e em consultas relacionais.
- **Redis para último estado** (sugestão do §9.1): desnecessário nesta escala;
  `ultimo_estado` no próprio Postgres atende sem consultar o histórico.

## Consequências
- Deduplicação e gravação da telemetria na mesma transação (Idempotent Receiver).
- Dado atrasado entra na posição certa da série.
- O banco é o teto de vazão observado no teste de carga (travas em
  `ultimo_estado` e `dispositivo` quando várias réplicas gravam os mesmos
  dispositivos).
