# Phase 4 evidence (serving, containers, frontend)

Captured 2026-11-24 on branch `feat/phase-4-serving`, against the **real champion model** (`credit-risk-default-model` v1,
random forest, test ROC-AUC 0.7883), not a stand-in. Design and usage: [`docs/serving.md`](../../serving.md).

| Exit criterion / deliverable | Evidence | Result |
|---|---|---|
| API works end to end in a container | [curl_transcript.txt](curl_transcript.txt) | valid single and batch requests score; malformed JSON 400, empty 400, wrong content type 415, oversize 413, bad fields 422 (field named), no key 401, wrong key 401; container healthy, uid 10001 |
| Whole stack comes up | [stack_check.txt](stack_check.txt) | `docker compose` (base + dev): api, frontend, postgres, mlflow, prometheus, grafana all running; frontend proxy to API to model works; Prometheus scrapes the API (target `up`, counters visible); Grafana datasource provisioned; MLflow serves from Postgres |
| UI scores a customer; key not visible to the browser | [screenshots/](screenshots/) (9 images), `frontend/e2e/smoke.spec.ts` | screenshots below; Playwright proves no request, header, body, HTML or script contains the key while the server still sends it |
| BentoML | [bento_run.txt](bento_run.txt) | Bento built and served once; the full 4,500-row test split scores ROC-AUC 0.7883 through it; invalid input rejected |
| Tests | [CI run 45317211962](https://github.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform/actions/runs/45317211962) | all green: lint 12 s, **tests 7m31s** (parallel, ~485 tests incl. 67 API), security 49 s, gitleaks 10 s, Docker 2m09s, frontend 57 s (14 unit + 7 browser tests), `CI passed`. API tests also pass in a 37-package environment that mirrors the image |
| Images pass Trivy | same run, `Docker build + Trivy scan` | both images: 0 HIGH/CRITICAL with a fix (vulnerabilities and embedded secrets); the job also boots the API with no model (503), checks 401 without a key and uid 10001 |
| Second machine | **pending** | needs a teammate to run `docker compose up` from the README |

## Screenshots
1. `01-score-empty.png` the score page (form built from the API's schema; disclaimer banner)
2. `02-score-struggling-higher-risk.png` risk score 0.85, **Higher risk**, gauge and the factors that moved it
3. `03-score-reliable-lower-risk.png` risk score 0.14, **Lower risk**
4. `04-validation-error.png` out-of-range input caught in the browser, nothing sent
5. `05-model-and-data.png` serving model, test metrics, quality gate, accepted inputs
6. `06-monitoring.png` live API health and tool links
7. `07-admin.png` champion info; promote/rollback disabled until Phase 8
8. `08-dark-mode.png` dark colour scheme
9. `09-phone-width.png` 390 px layout

## Problems found by running the real thing (and fixed in this branch)
| Problem | How it was found | Fix |
|---|---|---|
| `/metrics` demanded an API key (Flask names the endpoint after the function) | first API test run | endpoint named explicitly |
| MLflow container crash-looped: SQLAlchemy 2.1 maps `postgresql://` to the psycopg v3 driver, not installed | full-stack run | URI names `postgresql+psycopg2://` |
| Plain `docker build` baked the model in (last stage is the default target); fails in CI where `models/` is absent | reproduced CI's failure in a clean clone | model-free `runtime` is now the last (default) stage |
| `frontend/public/` was empty, so Git dropped it and the image build could not copy it | same clean-clone reproduction | `.gitkeep` |
| `package-lock.json` generated beside a Windows `node_modules` lacked Linux native packages (vitest failed on Linux) | same clean-clone reproduction, run in a Linux Node container | regenerated from a clean folder; `npm ci` verified on Windows, Alpine, Debian |
| Trivy flagged fixable HIGH findings in tools the running containers never use: `wheel` and setuptools' vendored `jaraco.context` (API image); npm's bundled `brace-expansion`, `ip-address`, `pacote` (frontend image) | CI's Trivy step, reproduced locally with the same scanner | removed those tools from the runtime images (smaller attack surface) instead of suppressing the findings; both scan clean and were re-run to prove they still serve |
| Test job had grown to 11m36s (target under 10) | CI timing | pytest-xdist, one worker per module: 418 tests in about 5 min locally, 7m31s on the GitHub runner |
| First `/explain` call took 6.5 s (one-time SHAP compilation) and one-row scoring took 49 ms | latency measurement | start-up warm-up in a background thread; estimator single-threaded (26 ms) |

## Honest limits
- The model's score is **not a calibrated probability** (review finding 1). The API and UI call it a risk score and say so.
- Image size is about 1.3 GB on disk (SHAP's numba/llvmlite, pyarrow, scipy): the cost of `/explain`.
- Scoring by customer id (Feast online store) is not exposed yet; the API takes raw fields.
- The cross-machine test needs a second person.
