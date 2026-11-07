# MEMORY.md — Project State and Decisions

Living log for the team and AI agents. Keep it short and current. Newest entries at the top of each section. Facts only; no secrets.

_Last updated: 2026-11-07_

## Current status
- **Phase:** 1 — Data, DVC (S1). Cycles 0-1 delivered; see `docs/testing/cycle-0.md` and `cycle-1.md`. Real-UCI manual check pending.
- **Repo state:** git repo exists (2 commits on `main`). S1 Cycles 0-1 arrive as tarballs, committed on feature branches (`feat/s1-cycle0-skeleton`, `feat/s1-cycle1-data`) via PR.
- **Built:** planning docs; S1 Cycle 0: `src/common/` (config, seed, logging, stage CLI + exit codes), `params.yaml`, `configs/`, pinned requirements, `docs/contracts-s1.md`, `tests/test_common.py`. S1 Cycle 1: `src/data/{ingestion,validation,schema,synthetic,paths}.py`, `src/common/io.py`, `dvc.yaml` (ingest -> validate), `.dvc/`, `docs/dvc-runbook.md`, `tests/test_data.py`. 159 tests, 99% coverage of `src/`.
- **Next up:**
  1. S1: run the Cycle 1 manual checks on real UCI data (`docs/testing/cycle-1.md`), then commit `dvc.lock` and the reports.
  2. S1: Cycle 2 (preprocessing, baseline model, MLflow logging, model v0 for serving).
  3. Hashir: S3 bucket for the DVC remote, AWS budget alert, OIDC provider; CI job per `docs/contracts-s1.md` section 9.
  4. Instructor sign-off on problem and dataset.

## Decisions log
| Date | Decision | Why |
|---|---|---|
| 2026-11-05 | Problem: credit-card default prediction on UCI "Default of Credit Card Clients" (30k rows, CC BY 4.0) | Small and cheap to train in CI; clear licence; has an ID for Feast and sensitive attributes for fairness work. |
| 2026-11-05 | Serve with **Flask** + gunicorn, not FastAPI | Spec text mandates Flask; the spec's repo tree says FastAPI (treated as a typo). |
| 2026-11-05 | Orchestration: Airflow → Kubeflow Pipelines on local kind/Minikube | Spec requires dual orchestration; local K8s avoids EKS cost. |
| 2026-11-05 | AWS compute: ECS Fargate (0.25 vCPU / 0.5 GB), no EKS/NAT | Cost control. K8s scaling is demonstrated locally. |
| 2026-11-05 | Frontend: Next.js with server-side proxy to Flask | Better demo; API key never reaches the browser. Not graded, so it is time-boxed with a Swagger fallback. |
| 2026-11-05 | Added Feast, BentoML, security scanning | Rubric scores them though the spec body doesn't mention them. |
| 2026-11-05 | Hugging Face limited to model-card hosting (pending approval) | Tabular problem; HF is "where appropriate". |
| 2026-11-05 | CODEOWNERS wins over `plan.md` roles: JA-Codex owns data/features/models/feature_repo + `tests/`, `dvc.yaml`, `.dvc/config`; Hashir-Rauf owns frontend, pipelines, CI, serving, infra, monitoring | Team decision; plan/agents docs updated to match. |
| 2026-11-06 | Registry promotion via MLflow aliases `challenger` / `champion`; no stages, no `Production` alias | MLflow stages are deprecated; one naming across docs. |
| 2026-11-06 | UCI has no row timestamps: Feast `event_timestamp` and drift batches are synthetic and labelled simulated | Dataset is a single snapshot; monthly columns are per-customer history. |
| 2026-11-06 | `SEX`, `MARRIAGE`, `AGE` excluded from model inputs; used only for fairness reporting | rules.md §2. |
| 2026-11-06 | Stage CLIs share exit codes: 0 ok, 1 error, 2 validation failed, 3 gate failed | Lets Airflow/KFP branch without parsing logs. See `docs/contracts-s1.md`. |
| 2026-11-07 | Data versions tracked by the `dvc.yaml` pipeline and `dvc.lock` (no `dvc add` pointer files); JSON reports are `cache: false` and live in git | One pointer file to review; readable PR diffs. |
| 2026-11-07 | `--smoke` = synthetic data under `data/smoke/` only; `data.source: synthetic` allowed for demos/tests, never for reported results | CI needs no data or credentials; tracked data cannot be overwritten. |
| 2026-11-07 | Validation: real-but-undocumented UCI codes (EDUCATION 0/5/6, MARRIAGE 0, PAY -2/0) only warn; any other violation fails with exit 2 | Matches the actual dataset while still blocking bad data. |
| 2026-11-07 | Excel read via python-calamine, not xlrd | pandas 3.1 deprecates the xlrd engine. |

## Assumptions to confirm
- [ ] Instructor accepts a tabular credit-risk problem, with Hugging Face limited to model-card hosting.
- [ ] Feast and BentoML are expected deliverables (rubric lists them).
- [ ] FastAPI in the spec's repo tree is a typo for Flask.
- [ ] Kubeflow on local kind is acceptable for the demo.
- [ ] Initial quality gate ROC-AUC ≥ 0.75 is reasonable; revisit after the baseline.
- [ ] Python 3.11 is the team runtime (verified on 3.11.18 / Linux; not yet on Windows).
- [ ] The real UCI zip downloads and parses (calamine reader), and the validation thresholds fit it (Cycle 1 manual check 2). Not verifiable from the dev sandbox.

## Open questions / blockers
- None yet.

## Resources (fill in as created; no credentials here)
| Item | Value |
|---|---|
| GitHub repo | _TBD_ |
| DagsHub repo | _TBD_ |
| DVC remote (S3 bucket) | _TBD_ |
| MLflow tracking URI | _TBD_ |
| AWS region / account alias | _TBD_ |
| ECR repo / ECS cluster | _TBD_ |
| API endpoint | _TBD_ (stopped when not demoing) |
| Budget alert configured? | No |

## Metrics baseline (fill after first runs)
| Model | ROC-AUC | PR-AUC | Notes |
|---|---|---|---|
| Logistic Regression | _TBD_ | _TBD_ | baseline |

## Lessons learned / gotchas
- Windows CRLF checkouts made every tracked file show as modified. `.gitattributes` (`eol=lf`) added; run `git add --renormalize .` once.
- argparse exits 2 on bad usage, which collides with our "validation failed" code; `stage_main` remaps it to 1.
- `dvc checkout` refuses to overwrite hand-edited tracked files; use `--force`.
- Directory deps such as `src/common` need `.dvcignore` entries for `__pycache__`, otherwise stage hashes churn.
- DVC merge conflicts: resolve `params.yaml` by hand, regenerate `dvc.lock` and reports with `dvc repro`; never hand-edit hashes (`docs/dvc-runbook.md`).
- pandera puts missing/extra column names in `failure_case`, not `column`; the validation report maps them back.
- _Add more: Kubeflow memory settings, AWS permission surprises._

## Changelog
- 2026-11-05 — Read spec; wrote `plan.md`; added Next.js frontend; created `.gitignore`, `agents.md`, `rules.md`, `memory.md`.
- 2026-11-06 — S1 Cycle 0: package skeleton, `src/common/`, `params.yaml`, `configs/`, pinned requirements, `.gitattributes`, `pyproject.toml`, contract + testing docs, 57 tests.
- 2026-11-07 — S1 Cycle 1: ingestion (uci/s3/local/synthetic), Pandera validation + reports, `dvc.yaml` ingest -> validate, `.dvc/`, DVC runbook with rehearsed reproducibility demo and merge-conflict handling, 159 tests.
