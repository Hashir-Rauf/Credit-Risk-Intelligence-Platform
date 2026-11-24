# syntax=docker/dockerfile:1
# Frontend image: Next.js standalone server, multi-stage, non-root, no secrets baked in.
# API_URL and API_KEY are supplied at run time (compose / the orchestrator), never at build time.

FROM node:22.23.5-alpine3.24 AS deps
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

FROM node:22.23.5-alpine3.24 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.23.5-alpine3.24 AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app -h /app app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
# /api/health answers 200 (API up) or 503 (API down); either means this server itself is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit([200,503].includes(r.status)?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
