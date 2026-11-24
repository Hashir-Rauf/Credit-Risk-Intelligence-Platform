# Serving (Phase 4)

Owner: `@Hashir-Rauf`. Contract with the model: [`contracts-s1.md`](contracts-s1.md) section 8d (owner `@JA-Codex`).
Evidence: [`docs/evidence/phase-4/`](evidence/phase-4/README.md).

```
browser ──► Next.js frontend (:3000) ──► Flask API (:8000) ──► models/model.joblib
              holds API_KEY (server only)     validates, scores, explains      (+ explainer background)
                                              /metrics ──► Prometheus ──► Grafana
BentoML service: the same model packaged as a Bento (alternate path, no auth of its own)
```

## The API (`src/serving/`)
| Endpoint | Auth | What it does |
|---|---|---|
| `GET /health` | open | 200 when a model is loaded, **503** when not (use as the readiness probe) |
| `GET /model-info` | key | Serving model: version, algorithm, threshold, git SHA, training-data md5, test metrics, quality gate, the 20 accepted inputs |
| `POST /predict` | key | One record `{...fields}` or a batch `{"records": [...]}` (max 100) → `risk_score` and `prediction` per record |
| `POST /explain?top_k=5` | key | Per-record top factors (SHAP contributions in log-odds). Max 5 records |
| `GET /metrics` | open | Prometheus text format. Keep it on the internal network |

Auth is the `X-API-Key` header, compared in constant time. The service **refuses to start** without `API_KEY` (16+
characters) unless `API_ALLOW_ANONYMOUS=1` is set explicitly (development only).

### Request and response
```jsonc
// POST /predict   (extra keys such as SEX are ignored and reported by name only)
{ "LIMIT_BAL": 50000, "EDUCATION": 3, "PAY_1": 1, "PAY_2": 2, "PAY_3": 0, "PAY_4": 0, "PAY_5": 0, "PAY_6": 0,
  "BILL_AMT1": 49000, "BILL_AMT2": 48000, "BILL_AMT3": 47000, "BILL_AMT4": 46000, "BILL_AMT5": 45000, "BILL_AMT6": 44000,
  "PAY_AMT1": 1500, "PAY_AMT2": 1000, "PAY_AMT3": 1000, "PAY_AMT4": 1200, "PAY_AMT5": 1000, "PAY_AMT6": 1000 }
// 200
{ "request_id": "…", "model_version": "1", "threshold": 0.5, "score_type": "uncalibrated_risk_score",
  "predictions": [ { "risk_score": 0.612, "prediction": 1 } ], "ignored_fields": [], "disclaimer": "…" }
```
Validation rules (types, ranges, allowed values) are generated from `reports/feature_schema.json`, the same file the model
was built against, so they cannot drift. The sensitive attributes `SEX`, `MARRIAGE`, `AGE` are **not** inputs.

### Errors (always JSON, always with a `request_id`, never a stack trace)
| Status | When |
|---|---|
| 400 | body is not valid JSON, empty, not an object, `records` empty or over the batch limit |
| 401 | missing or wrong `X-API-Key` |
| 404 / 405 | unknown route / wrong method |
| 413 | body larger than `MAX_BODY_BYTES` (1 MB) |
| 415 | not `Content-Type: application/json` |
| 422 | a field is missing, not a number, not whole when it must be, out of range, or not allowed. `error.details[]` lists `{index, errors:[{field, code, message}]}` |
| 500 | unexpected error: generic message plus the `request_id` to look up in the logs |
| 503 | no model loaded (`/health` says why) or explainer unavailable |

### What is never logged or counted
Raw field values never reach logs or metric labels (only route templates, status codes, model version, counts and score
buckets). A test asserts this. `X-Request-ID` is echoed when it is a short safe token, otherwise replaced.

## About the score: it is not a probability
The champion is a class-weighted random forest. Its output ranks customers well (ROC-AUC 0.788) but is **not calibrated**
(mean score 0.40 vs 22% real defaults; see [`reviews/phase-2-review.md`](reviews/phase-2-review.md) finding 1).
So the API returns `risk_score` (never `probability`), says so in every response and in `/model-info`
(`score.calibrated: false`), and the UI shows a band, not a percentage. When the model is calibrated, a `probability`
field can be added without breaking clients.

## Configuration (environment only)
`API_KEY` (required), `MODEL_PATH` (default `models/model.joblib`), `EXPLAINER_BACKGROUND_PATH`, `FEATURE_SCHEMA_PATH`,
`MODEL_URI` (load from the MLflow registry instead; needs mlflow in the image), `MODEL_VERSION`, `GIT_SHA`, `MAX_BATCH`,
`MAX_BODY_BYTES`, `EXPLAIN_MAX_RECORDS`, `WARMUP_EXPLAIN` (default on), `LOG_LEVEL`. Defaults are in
`src/serving/settings.py`.

## Run it
```bash
dvc pull -r dagshub                                   # model files into models/
export API_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 4 src.serving.wsgi:app   # Linux/macOS
# Windows has no gunicorn: python -m flask --app src.serving.wsgi run --port 8000
```
**Whole stack** (API, frontend, MLflow + Postgres, Prometheus, Grafana):
```bash
cp .env.example .env            # set API_KEY, POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD
dvc pull -r dagshub
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build     # dev: all ports on localhost
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d         # prod-style: hardened, fewer ports
```
Ports (all bound to `127.0.0.1`): API 8000, frontend 3000, Grafana 3001; dev adds MLflow 5000, Prometheus 9090, Postgres 5432.

## Images
| Image | Base | Notes |
|---|---|---|
| `docker/Dockerfile.api` | `python:3.11-slim-bookworm` | Multi-stage, non-root (uid 10001), healthcheck. Installs only the 37 packages in `requirements-serve.txt`, using `requirements.lock.txt` as **constraints**, so versions equal the ones the model was trained with. Target `runtime` has no model (mount `./models`); target `with-model` bakes it in for deployment. About 1.3 GB on disk, mostly SHAP's numba/llvmlite (208 MB), pyarrow (170 MB) and scipy (143 MB): the cost of `/explain` |
| `frontend/Dockerfile` | `node:22.23.5-alpine3.24` | Next.js standalone server, non-root, 266 MB |
| `docker/Dockerfile.mlflow` | `python:3.11-slim-bookworm` | MLflow server for the local stack, Postgres backend |

One gunicorn worker with four threads: metrics stay exact in-process, and the estimator is single-threaded on purpose
(measured 49 → 26 ms for a one-row request). Scale with replicas (Phase 7), not workers.

## BentoML
`bentofile.yaml` + `src/serving/bento_service.py` build the **same model** into a Bento
(`bentoml build -f bentofile.yaml`; then `bentoml serve src.serving.bento_service:CreditRisk` or `bentoml containerize`).
It reuses the API's loader and validation, so scores are identical (a test asserts it). It has **no authentication**
of its own: put a gateway in front. Flask is the primary path because it adds auth, metrics, request ids and `/explain`.

## Known limitations
- Scoring by customer id (Feast online store) is not exposed; the API takes raw fields. Needs Feast in the image.
- No rate limiting yet (Phase 9 security pass). Body and batch sizes are capped.
- `/metrics` is open; do not publish it outside the compose network.
- `MODEL_URI` loading is implemented and unit-tested with a stub, but not exercised against DagsHub from inside a container.
