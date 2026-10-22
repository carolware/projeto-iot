# ADR 0002 — Mosquitto como broker MQTT e barramento interno

## Contexto
Dispositivos falam MQTT (RF-ING-01), e a borda precisa de um broker local leve
(RNF-06). O PDF sugere Kafka, NATS ou RabbitMQ como barramento interno (§9.1).

## Decisão
**Eclipse Mosquitto 2.0** em todo lugar: um broker central e um por nó de
borda. Os tópicos `plataforma/...` do broker central fazem o papel de barramento
interno (mensagens aceitas, alarmes, DLQ).

## Alternativas
- **EMQX**: mais recursos (cluster, regras, autenticação por banco), mas pesado
  para a borda e mais um componente para aprender.
- **Kafka como barramento**: durável e escalável, mas exige mais memória e
  operação do que uma PoC em uma máquina comporta.
- **NATS**: leve, mas seria mais um protocolo além do MQTT.

## Consequências
- Uma tecnologia só para dispositivos, borda e barramento; a bridge nativa do
  Mosquitto liga a borda à central.
- O barramento não tem retenção longa: quem perde mensagens enquanto está fora
  as recupera pelo banco, que é o registro durável.
- Escala horizontal da ingestão por assinatura compartilhada (`$share`).
