# MEMORY.md — Project State and Decisions

Living log for the team and AI agents. Keep it short and current. Newest entries at the top of each section. Facts only; no secrets.

_Last updated: 2026-11-05_

## Current status
- **Phase:** 0 — Setup. S1 Cycle 0 (skeleton, config, seed, stage CLI) delivered; see `docs/testing/cycle-0.md`.
- **Repo state:** git repo exists (2 commits on `main`). S1 Cycle 0 arrives as a tarball to be committed on `feat/s1-cycle0-skeleton` via PR.
- **Built:** planning docs; S1 Cycle 0: `src/common/` (config, seed, logging, stage CLI + exit codes), `params.yaml`, `configs/`, pinned requirements, `docs/contracts-s1.md`, `tests/test_common.py` (57 tests, 100% coverage of `src/`).
- **Next up:**
  1. Instructor sign-off on problem and dataset.
  2. `git init`, create the GitHub repo, enable branch protection, add the skeleton layout.
  3. AWS account: budget alert, then the OIDC provider.

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

## Assumptions to confirm
- [ ] Instructor accepts a tabular credit-risk problem, with Hugging Face limited to model-card hosting.
- [ ] Feast and BentoML are expected deliverables (rubric lists them).
- [ ] FastAPI in the spec's repo tree is a typo for Flask.
- [ ] Kubeflow on local kind is acceptable for the demo.
- [ ] Initial quality gate ROC-AUC ≥ 0.75 is reasonable; revisit after the baseline.
- [ ] Python 3.11 is the team runtime (Cycle 0 verified on 3.11.18 / Linux; not yet on Windows).

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
- _Add more: DVC merge-conflict steps, Kubeflow memory settings, AWS permission surprises._

## Changelog
- 2026-11-05 — Read spec; wrote `plan.md`; added Next.js frontend; created `.gitignore`, `agents.md`, `rules.md`, `memory.md`.
- 2026-11-06 — S1 Cycle 0: package skeleton, `src/common/`, `params.yaml`, `configs/`, pinned requirements, `.gitattributes`, `pyproject.toml`, contract + testing docs, 57 tests.
