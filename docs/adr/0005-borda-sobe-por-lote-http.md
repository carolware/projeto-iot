# ADR 0005 — Borda sobe por lote HTTP e recebe comandos pela bridge MQTT

## Contexto
A sincronização precisa ser sem perdas nem duplicatas e ordenada pelo horário
da medição (RF-EDG-02), com alarmes antes da telemetria (RF-EDG-05).

## Decisão
- **Subida**: o nó de borda envia lotes de até 500 mensagens para
  `POST /api/v1/ingestao/lote`, que devolve o resultado de cada mensagem. Só sai
  da fila o que a central confirmou (`aceita` ou `duplicada`). O fluxo normal e
  a sincronização depois de uma queda são o mesmo caminho.
- **Descida**: comandos da central chegam pela **bridge do Mosquitto**
  (`cleansession false`, QoS 1), que guarda o que for emitido com o link fora.

## Alternativas
- **Encaminhar por MQTT QoS 1 (bridge nos dois sentidos)**: o PUBACK confirma a
  entrega ao broker, não a gravação no banco, e não informa por que uma mensagem
  foi recusada.
- **Lote sobre MQTT**: precisaria de um protocolo próprio de confirmação.

## Consequências
- Confirmação ponta a ponta, com o motivo de cada recusa (vai para a DLQ da
  borda).
- A central deduplica pelo `id_mensagem`: reenviar um lote inteiro é seguro.
- Não implementado: compressão do corpo do lote (RNF-05), registrada na
  [rastreabilidade](../rastreabilidade.md). A ingestão HTTP local na borda
  (RF-EDG-01) foi feita depois: entra na mesma fila que o MQTT e sobe pelos
  mesmos lotes.
