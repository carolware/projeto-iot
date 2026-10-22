# ── build ──────────────────────────────────────────────────────────────────
FROM node:24-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Variáveis VITE_* são embutidas no bundle durante o build; as do ambiente
# têm prioridade sobre o .env do front.
ARG VITE_API_URL=http://localhost:8000
ARG VITE_KEYCLOAK_URL=http://localhost:8080
ARG VITE_KEYCLOAK_REALM=sentinela
ARG VITE_KEYCLOAK_CLIENTE=painel
ENV VITE_API_URL=$VITE_API_URL \
    VITE_KEYCLOAK_URL=$VITE_KEYCLOAK_URL \
    VITE_KEYCLOAK_REALM=$VITE_KEYCLOAK_REALM \
    VITE_KEYCLOAK_CLIENTE=$VITE_KEYCLOAK_CLIENTE

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
