# AGENTS.md — Credit Risk Intelligence Platform

Orientation for AI coding agents (and new teammates) working in this repo.
Read this first, then `rules.md` (hard constraints) and `memory.md` (current state and decisions). The full roadmap is in `plan.md`; the source spec is `MLOps_Course_Project_Requirements.pdf`.

## What this project is
A course MLOps project (FAST-NUCES, Semester 7, 3 students). It predicts next-month credit-card default (UCI Default of Credit Card Clients) and demonstrates a **reproducible, automated, tested, deployed and monitored** ML system. The grade is for MLOps rigor, not model accuracy. Don't over-invest in modelling.

## Stack at a glance
| Concern | Tool |
|---|---|
| Versioning | Git/GitHub, DVC (remote: S3 + DagsHub) |
| Tracking / registry | MLflow (mirrored to DagsHub) |
| Feature store | Feast |
| Orchestration | Airflow (schedule, ingest, validate, trigger) → Kubeflow Pipelines on kind/Minikube (ML graph) |
| Serving | Flask + gunicorn REST API, BentoML packaging |
| Frontend | Next.js (thin client; server-side proxy to Flask) |
| CI/CD | GitHub Actions (OIDC to AWS) |
| Containers | Docker, Docker Compose, Kubernetes manifests |
| Cloud | AWS: ECR, ECS Fargate, S3, CloudWatch, IAM, Secrets Manager |
| Monitoring | Prometheus, Grafana, Evidently, CloudWatch |
| Tests / quality | pytest, ruff, black, bandit, pip-audit, Trivy, gitleaks |

## Repo map
```
src/            data/ features/ models/ serving/ monitoring/   (reusable modules, no logic in notebooks)
feature_repo/   Feast definitions
tests/          test_data, test_model, test_api, test_pipeline, test_regression
pipelines/      airflow/dags, kubeflow/{components,pipeline_definition.py,compiled_pipeline.yaml}
configs/        model_params.yaml, pipeline_config.yaml, monitoring.yaml
docker/         Dockerfile.api, Dockerfile.ml
frontend/       Next.js app
k8s/            deployment, service, hpa, IAM policy manifests
monitoring/     prometheus, alert rules, grafana dashboards
infra/aws/      provisioning and teardown scripts
.github/workflows/  ci.yml, deploy.yml, retrain-eval.yml
docs/           diagrams, risk assessment, demo script
```
Not everything exists yet. Check `memory.md` for what is built, and don't assume a file is present.

## Team ownership (anyone may edit anything, but ask the owner before large changes)
- **S1 — Data & ML:** data, validation, features, Feast, training/tuning/eval, MLflow, fairness, tests (data/features/model/regression), `dvc.yaml`. CODEOWNERS is authoritative where it differs from this list.
- **S2 — MLOps & Automation:** Git/GitHub, DagsHub, Airflow, Kubeflow, CI/CD, security scanning.
- **S3 — Deployment & Reliability:** Flask API, BentoML, Docker/Compose, Kubernetes, AWS, monitoring, frontend.

## How to work here
1. **Read before writing.** Check the existing module, config and tests for the area you're touching.
2. **Small, reviewable changes** on a feature branch. Never push to `main`. Open a PR, and CI must be green.
3. **Config over constants.** Hyperparameters, thresholds and paths live in `configs/` and `params.yaml`, not inline.
4. **Every behaviour change ships with a test.** Update `memory.md` when you finish something notable or make a decision.
5. **Prefer simple and cheap.** This is a student project with a tight AWS budget; choose the smallest thing that satisfies the spec.
6. **Report honestly.** If a test fails or a step was skipped, say so. Never claim something was verified without running it.

## Commands (target interface; update as they're built)
```
dvc repro                         # run data → train → evaluate pipeline
dvc pull / dvc push               # sync data with remote
pytest -q                         # all tests
ruff check . && black --check .   # lint / format
docker compose up --build         # api + mlflow + postgres + prometheus + grafana (+ frontend)
make help                         # preferred wrapper for the above once a Makefile exists
```

## Do not do (see `rules.md` for the full list)
- Commit data, models, credentials or `.env` files.
- Hard-code secrets, account IDs or keys anywhere.
- Deploy a model that failed its quality gate, or auto-promote every new model.
- Create or leave running billable AWS resources without being asked, and without a teardown path.
- Run destructive commands (force-push, `dvc gc`, deleting buckets/clusters) without explicit confirmation.
