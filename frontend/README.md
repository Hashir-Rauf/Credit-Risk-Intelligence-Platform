# Frontend

Next.js (App Router, TypeScript). Owner: `@Hashir-Rauf`. Full architecture: [`docs/serving.md`](../docs/serving.md).

**Security model:** the browser only talks to this app's own `/api/*` route handlers. They attach `X-API-Key` on the server
(`src/lib/api.ts` is `server-only`), so the key and the backend URL never reach client JavaScript. A Playwright test proves it.

## Pages
`/` score a customer (form built from the API's schema, example customers, gauge, top factors) ·
`/model` what is serving and how it was evaluated · `/monitoring` live health and tool links ·
`/admin` champion info; promote/rollback is disabled until Phase 8.

## Develop
```bash
cp .env.example .env.local      # API_URL, API_KEY (server-side only)
npm ci
npm run dev
npm run lint && npm run typecheck && npm test      # checks
npm run build && npx playwright install chromium && npm run e2e   # browser smoke test against a mock API
```

## Lock file
`package-lock.json` must list the native optional packages of **every** platform (Windows, macOS, Linux glibc and musl),
because developers run Windows, CI runs Ubuntu and the Docker image runs Alpine. npm only does that when the lock is
generated from a folder that has **no `node_modules`** (an existing install makes npm record only what it installed).
So never regenerate it in place; use a clean folder:
```bash
mkdir /tmp/lock && cp package.json .npmrc /tmp/lock && cd /tmp/lock
docker run --rm -v "$PWD:/w" -w /w node:22.23.5-bookworm-slim npm install --package-lock-only --no-audit --no-fund
cp package-lock.json <repo>/frontend/package-lock.json
```
`npm ci` then works on Windows, macOS and Linux (verified on Windows, Alpine and Debian). Dependencies are pinned to exact
versions (`.npmrc`: `save-exact=true`).
