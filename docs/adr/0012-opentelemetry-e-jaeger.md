# ADR 0012 — OpenTelemetry com Jaeger para traces distribuídos

## Contexto
O RF-OBS-03 pede rastreamento distribuído. Até aqui, a correlação entre borda e
central era feita pelo `id_mensagem` nos logs JSON (RF-OBS-02): funciona para
uma mensagem, mas não mostra onde o tempo é gasto (rede, API, pipeline, SQL).

## Decisão
- **OpenTelemetry SDK** em Python na central, na ingestão MQTT e nos nós de
  borda (`nucleo/rastreamento.py`), exportando por **OTLP/HTTP** para um
  **Jaeger all-in-one** no compose (UI em `:16686`).
- Instrumentação automática de FastAPI, `requests` e SQLAlchemy, mais spans
  próprios: `borda.receber`, `borda.sincronizar_lote` e `ingestao.pipeline`.
- O lote HTTP borda→central leva o cabeçalho W3C `traceparent` (requests
  instrumentado): um trace começa na borda e continua na central até o SQL.
- MQTT 3.1.1 não tem cabeçalhos: mensagem que chega por MQTT começa um trace
  novo na ingestão. A ligação com a origem continua sendo o `id_mensagem`, que
  vai como atributo do span; os logs JSON ganham o `trace_id`.
- Amostragem pelas variáveis padrão (`OTEL_TRACES_SAMPLER=parentbased_traceidratio`,
  `OTEL_TRACES_SAMPLER_ARG` ← `OTEL_AMOSTRAGEM` do `.env`). Sem
  `OTEL_EXPORTER_OTLP_ENDPOINT`, o rastreamento fica desligado (testes, execução local).

## Alternativas
- **Só logs com `id_mensagem`** (o que havia): sem duração por etapa.
- **Collector do OpenTelemetry na frente do Jaeger**: mais flexível (vários
  destinos, amostragem na cauda), mas um contêiner a mais sem ganho na PoC.
- **MQTT 5 com propriedades de usuário** para propagar o contexto também pelo
  MQTT: exigiria trocar o protocolo dos dispositivos e do paho (ADR 0002).

## Consequências
- Na UI do Jaeger dá para abrir um lote da borda e ver rede, rota, pipeline e
  cada consulta SQL no mesmo trace.
- O `SQLAlchemyInstrumentor` ainda não aceita SQLAlchemy 2.1; usamos o
  `EngineTracer` do mesmo pacote direto, engine a engine.
- Com amostragem 1.0 cada mensagem MQTT gera um trace na borda: para o teste
  de carga, baixe `OTEL_AMOSTRAGEM` (ex.: 0.01) ou desligue o endpoint.
- O Jaeger all-in-one guarda em memória: os traces somem ao reiniciar.
