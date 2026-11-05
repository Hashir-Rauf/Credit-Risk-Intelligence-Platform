# MEMORY.md — Project State and Decisions

Living log for the team and AI agents. Keep it short and current. Newest entries at the top of each section. Facts only; no secrets.

_Last updated: 2026-11-05_

## Current status
- **Phase:** 0 — Setup (planning complete, no code yet).
- **Repo state:** local folder only, not yet a git repository. Present: `plan.md`, `agents.md`, `rules.md`, `memory.md`, `.gitignore`, spec PDF.
- **Built:** nothing beyond the planning docs.
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

## Assumptions to confirm
- [ ] Instructor accepts a tabular credit-risk problem, with Hugging Face limited to model-card hosting.
- [ ] Feast and BentoML are expected deliverables (rubric lists them).
- [ ] FastAPI in the spec's repo tree is a typo for Flask.
- [ ] Kubeflow on local kind is acceptable for the demo.
- [ ] Initial quality gate ROC-AUC ≥ 0.75 is reasonable; revisit after the baseline.

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
- _None yet. Add things like DVC merge-conflict steps, Kubeflow memory settings, or AWS permission surprises._

## Changelog
- 2026-11-05 — Read spec; wrote `plan.md`; added Next.js frontend; created `.gitignore`, `agents.md`, `rules.md`, `memory.md`.
