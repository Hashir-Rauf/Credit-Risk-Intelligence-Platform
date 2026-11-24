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
npm install                     # Windows/macOS developers: use `install`, not `ci` (see below)
npm run dev
npm run lint && npm run typecheck && npm test      # checks
npm run build && npx playwright install chromium && npm run e2e   # browser smoke test against a mock API
```

## Lock file
`package-lock.json` is generated on **Linux** (CI and the Docker build run `npm ci` there). npm records native optional
packages only for the platform that generated the lock, so `npm ci` on Windows or macOS reports "Missing: ... from lock
file". Use `npm install` locally. To refresh the lock, regenerate it in Linux, not on your laptop:
```bash
docker run --rm -v "$PWD:/app" -w /app node:22.23.5-alpine3.24 sh -c "rm -f package-lock.json && npm install --package-lock-only --no-audit --no-fund"
```
Dependencies are pinned to exact versions (`.npmrc`: `save-exact=true`).
