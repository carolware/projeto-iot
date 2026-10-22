#!/usr/bin/env sh
# link.sh — corta ou restaura o link entre um nó de borda e a central,
# simulando a queda do satélite/celular rural (RF-EDG-01/02, RNF-04).
#
# A borda e o broker local saem da rede "nuvem"; as estações continuam
# falando com eles pela rede da região, e as leituras se acumulam na fila.
#
# Uso:  sh plataforma/simuladores/demo/link.sh off tocantins
#       sh plataforma/simuladores/demo/link.sh on  tocantins
set -eu

acao=${1:?uso: link.sh on|off tocantins|goias}
regiao=${2:?uso: link.sh on|off tocantins|goias}
rede="$(docker compose ps -q central | xargs docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | tr ' ' '\n' | grep nuvem)"

for servico in "borda-$regiao" "mosquitto-borda-$regiao"; do
  conteiner="$(docker compose ps -q "$servico")"
  case "$acao" in
    off) docker network disconnect "$rede" "$conteiner" && echo "✂  $servico desconectado da central" ;;
    on)  docker network connect "$rede" "$conteiner" && echo "🔗 $servico reconectado à central" ;;
    *)   echo "ação inválida: $acao (use on|off)" >&2; exit 1 ;;
  esac
done
