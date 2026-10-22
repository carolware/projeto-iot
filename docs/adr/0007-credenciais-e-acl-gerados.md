# ADR 0007 — Senhas e ACL do Mosquitto geradas a partir do cadastro

## Contexto
Cada dispositivo precisa de credencial única e revogável (RF-DIS-02), restrita
aos próprios tópicos, na central e na borda (RF-SEG-04), inclusive com a borda
sem link.

## Decisão
A central gera o `password_file` (hashes `$7$`, PBKDF2-SHA512) e o `acl_file`
do Mosquitto a partir do cadastro e os reescreve quando algo muda; o broker
relê com SIGHUP. Cada nó de borda baixa a parte dele e a guarda no disco.

## Alternativas
- **Plugin dynamic-security do Mosquitto**: configurado por mensagens MQTT; a
  borda dependeria de sincronizar comandos administrativos.
- **mosquitto-go-auth contra o Postgres**: a borda sem link não alcançaria o
  banco da central.
- **Certificado X.509 por dispositivo**: mais forte, mas exige emitir, distribuir
  e revogar certificados (CRL), o que é demais para a PoC.

## Consequências
- Funciona offline na borda; só hashes circulam.
- Revogar = tirar a linha do arquivo: a conexão seguinte é recusada.
- Dispositivos simulados recebem credencial derivada por HMAC de
  `SEGREDO_PROVISIONAMENTO`, porque não há fábrica para gravá-la.
