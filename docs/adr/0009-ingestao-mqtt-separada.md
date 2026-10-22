# ADR 0009 — Ingestão MQTT em serviço separado da API

## Contexto
No teste de carga (A6), com a ingestão MQTT, o motor de regras, o WebSocket e a
API num só processo Python, a central saturava um núcleo de CPU (GIL) por volta
de 600 msg/s e o broker descartava mensagens acima da fila padrão.

## Decisão
- A ingestão MQTT roda no serviço **`central-ingestao`** (`python -m
  central.ingestao_mqtt`), em **micro-lotes** (uma transação por lote).
- As mensagens aceitas vão para o barramento `plataforma/{tipo}/...` (QoS 0),
  com a instância de origem no contexto. A API ouve o barramento e alimenta o
  motor e o tempo real, ignorando o que ela mesma publicou (lote HTTP).
- Réplicas da ingestão dividem as mensagens por assinatura compartilhada
  (`$share/ingestao/...`), cada uma com sessão persistente própria.
- O modo embutido (API ingerindo MQTT) continua sendo o padrão fora do compose,
  usado nos testes.

## Alternativas
- **Mais otimização no mesmo processo**: ajudou (validação com jsonschema-rs,
  cache de alarmes abertos, executemany), mas não tira o teto de um núcleo.
- **Kafka entre ingestão e motor**: durável, mas pesado para a PoC (ADR 0002).

## Consequências
- Nenhuma mensagem perdida a 1.000 msg/s; vazão sustentada de ~750 msg/s e p95
  de ~6 s. A meta do RNF-02 é atendida abaixo de ~700 msg/s.
- Barramento em QoS 0: se a API estiver fora, o motor não avalia o que chegou
  nesse intervalo (os dados estão no banco).
- Com duas réplicas, a vazão piorou por disputa de travas no Postgres; o compose
  usa uma.
- Cada réplica precisa de client id **fixo**. Com o hostname como id, recriar o
  contêiner deixou sessões persistentes órfãs no grupo `$share`, e o broker
  continuou enfileirando para elas parte das mensagens (perdidas para a
  plataforma). Corrigido com `CENTRAL_MQTT_CLIENT_ID` fixo; ao reduzir réplicas,
  é preciso apagar a sessão da que saiu ([operação §11](../operacao.md#11-problemas-conhecidos)).
- A equipe decidiu não otimizar além disso (PoC).
