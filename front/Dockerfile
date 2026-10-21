# ── build ──────────────────────────────────────────────────────────────────
FROM node:24-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Variáveis VITE_* são embutidas no bundle durante o build; as do ambiente
# têm prioridade sobre o .env do front.
ARG VITE_DATA_MODE=mqtt
ARG VITE_MQTT_URL=ws://localhost:9001
ARG VITE_MQTT_TOPIC_BASE=sentinela-iot-2026-joao-carol/monitoramento-br
# usuário "painel" do broker: só lê os tópicos legados (TEMPORÁRIO até a A5)
ARG VITE_MQTT_USUARIO=
ARG VITE_MQTT_SENHA=
ENV VITE_DATA_MODE=$VITE_DATA_MODE \
    VITE_MQTT_URL=$VITE_MQTT_URL \
    VITE_MQTT_TOPIC_BASE=$VITE_MQTT_TOPIC_BASE \
    VITE_MQTT_USUARIO=$VITE_MQTT_USUARIO \
    VITE_MQTT_SENHA=$VITE_MQTT_SENHA

# Fora do sandbox do Lovable, NITRO_PRESET sobrepõe o default (cloudflare-module)
# do plugin @lovable.dev/vite-tanstack-config, gerando um server Node comum.
ENV NITRO_PRESET=node-server
RUN npm run build

# ── runtime ────────────────────────────────────────────────────────────────
FROM node:24-slim AS runtime

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

COPY --from=build /app/.output ./.output

EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
