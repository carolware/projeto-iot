#!/bin/sh
# iniciar.sh — entrypoint dos brokers Mosquitto da plataforma (central e bordas).
#
# 1. Monta a configuração a partir do modelo (substitui ${BRIDGE_SENHA}).
# 2. Sobe o Mosquitto.
# 3. Vigia as senhas/ACL geradas pela plataforma (volume /mosquitto/acesso) e
#    manda SIGHUP quando mudam: dispositivo novo ou revogado vale sem reiniciar.
set -eu

ACESSO=/mosquitto/acesso
mkdir -p "$ACESSO"
for arquivo in senhas acl; do
  [ -f "$ACESSO/$arquivo" ] || : > "$ACESSO/$arquivo"
done

ajustar() {
  chown mosquitto:mosquitto "$ACESSO/senhas" "$ACESSO/acl"
  chmod 0600 "$ACESSO/senhas" "$ACESSO/acl"
}
assinatura() {
  cat "$ACESSO/senhas" "$ACESSO/acl" 2>/dev/null | md5sum
}

ajustar
sed "s|\${BRIDGE_SENHA}|${BRIDGE_SENHA:-}|g" /mosquitto/config/mosquitto.conf.modelo > /tmp/mosquitto.conf
chmod 0644 /tmp/mosquitto.conf

mosquitto -c /tmp/mosquitto.conf &
PID=$!
trap 'kill -TERM "$PID"; wait "$PID"; exit 0' TERM INT

ultima=$(assinatura)
while kill -0 "$PID" 2>/dev/null; do
  sleep 2
  atual=$(assinatura)
  if [ "$atual" != "$ultima" ]; then
    ultima=$atual
    ajustar
    kill -HUP "$PID"
    echo "iniciar.sh: senhas/ACL alteradas — configuração recarregada"
  fi
done
wait "$PID"
