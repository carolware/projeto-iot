# ── build ──────────────────────────────────────────────────────────────────
FROM node:24-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

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
