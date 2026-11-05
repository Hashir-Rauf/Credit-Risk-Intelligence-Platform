# Credit Risk Intelligence Platform — Project Plan

End-to-end MLOps system that predicts the probability a credit-card customer defaults next month, then serves, monitors and retrains that model. Source spec: `MLOps_Course_Project_Requirements.pdf`.

- **Team:** 3 students (roles in §3)
- **Problem:** binary classification, default risk scoring (maps to the "fraud / risk prediction" application area; confirm with the instructor)
- **Cloud:** AWS (ECR, ECS Fargate, S3, CloudWatch, IAM, Secrets Manager)
- **Priority:** the rubric grades MLOps rigor, not accuracy. Every decision below favours reproducible, automated, cheap and demonstrable.

---

## 1. Key Decisions

| Decision | Choice | Why |
|---|---|---|
| Dataset | **UCI Default of Credit Card Clients** (30k rows, 23 features, CC BY 4.0) | Small, so training and CI are cheap; licence is clear; has `ID` (Feast entity) and sensitive attributes (`SEX`, `AGE`, `MARRIAGE`) for the fairness/ethics section; time-ordered monthly columns make drift simulation easy. Stretch: Home Credit for a bigger run. |
| Models | Logistic Regression (baseline), Random Forest, XGBoost/LightGBM | Gives a real experiment comparison in MLflow with tuning (Optuna). |
| Primary metric | **ROC-AUC** and **PR-AUC**, plus recall at a fixed threshold | Class imbalance (~22% default). Gate: AUC ≥ 0.75 (tune after baseline). |
| Serving | Flask (gunicorn) REST API. BentoML packaging is optional but is in the rubric (§8 of spec), so add a Bento build step | Spec mandates Flask. Note: the spec's repo tree says FastAPI, the text says Flask. **Use Flask**. |
| Hugging Face | Optional: small HF text model is not natural for tabular. Use HF Hub as the **model/artifact card host** and a short explainability note instead. Confirm with the instructor that this is acceptable | Spec says "where appropriate". |
| Feature store | **Feast** (offline = parquet on S3/local, online = SQLite or Redis) | Rubric awards 6 marks. |
| Orchestration | **Airflow** (schedule, ingest, validate, trigger) → **Kubeflow Pipelines** (feature eng → train/tune → evaluate → register) on **kind/Minikube** | Dual orchestration is mandated; local K8s keeps cost at zero. |
| Tracking | MLflow (backend on SQLite/Postgres, artifacts on S3) mirrored to DagsHub | Spec requires both. |
| Monitoring | Prometheus + Grafana (ops), Evidently (drift/data quality), CloudWatch (AWS logs/infra) | All three are explicitly required. |
| UI | **Next.js** frontend calling Flask via server-side route handlers | Better demo than curl; keeps API key off the browser. Not graded, so timebox it and keep the Swagger fallback. |
| Security scanning | `bandit` (code), `pip-audit` / Trivy (deps + image), `gitleaks` (secrets) in CI | Rubric lists vulnerability scanning. |

---

## 2. Architecture

```
          GitHub (PR) ──► GitHub Actions CI ──► lint, pytest, security scan, docker build
                                │ pass
                                ▼
                       push image ► Amazon ECR ──► deploy ► ECS Fargate (Flask API)
                                                              │  /metrics  /predict
 S3 raw data ─► Airflow DAG ─► validate ─► trigger ─► Kubeflow Pipeline (kind)
 (DVC remote)   (schedule)     (fail=stop)            ├ A features + split
                                                      ├ B train + tune
                                                      ├ C evaluate vs threshold → MLflow
                                                      └ D package + register (Staging)
                                                                │
                    manual approval + champion/challenger ◄─────┘
                                │ promote (Production alias)
                                ▼
 Prometheus ◄── API metrics     Evidently drift job ─► Airflow retrain trigger
 Grafana ◄── Prometheus         CloudWatch ◄── ECS logs ──► alarm (errors/latency)
```

Fully automated: CI, data validation, training, evaluation gate, image build/push, staging deploy, drift detection, retraining *trigger*.
Manual gates: promotion to Production, rollback, and any retrain whose candidate fails to beat the champion.

---

## 3. Team Roles (everyone reviews everyone's PRs and must be able to defend all parts)

| Student | Area | Owns |
|---|---|---|
| **S1 — Data & ML** | Data and modelling | Dataset + licence check, cleaning, validation schema (Pandera/Great Expectations), feature engineering, Feast repo, training/tuning/evaluation modules, DVC data versioning, MLflow experiments, fairness analysis, **Next.js frontend** |
| **S2 — MLOps & Automation** | Pipelines and CI/CD | GitHub workflow + branch protection, DagsHub, DVC pipeline (`dvc.yaml`), Airflow DAGs, Kubeflow components/pipeline, pytest suite, GitHub Actions CI/CD, security scanning |
| **S3 — Deployment & Reliability** | Serving and infra | Flask API, BentoML, Dockerfiles, Compose, Kubernetes manifests + scaling demo, AWS (ECR/ECS/S3/IAM/CloudWatch/budget), Prometheus/Grafana, Evidently monitoring, alerts |

---

## 4. Repository Structure

Follows spec §5, adapted:

```
credit-risk-platform/
├── data/{raw,processed}/            # DVC-tracked, never in git
├── src/
│   ├── data/{ingestion,validation}.py
│   ├── features/{preprocessing,feast_features}.py
│   ├── models/{train,tune,evaluate,registry}.py
│   ├── serving/{app.py,schemas.py,model_loader.py}
│   └── monitoring/{monitor,drift,metrics}.py
├── feature_repo/                    # Feast definitions + feature_store.yaml
├── tests/{test_data,test_model,test_api,test_pipeline,test_regression}.py
├── pipelines/
│   ├── airflow/dags/{ml_orchestrator_dag,retrain_dag}.py
│   └── kubeflow/{components/,pipeline_definition.py,compiled_pipeline.yaml}
├── configs/{model_params,pipeline_config,monitoring}.yaml
├── docker/{Dockerfile.api,Dockerfile.ml}
├── monitoring/{prometheus.yml,alert_rules.yml,grafana/dashboards/}
├── frontend/                        # Next.js UI (thin client; server-side proxy to Flask)
│   ├── app/{score,model,monitoring,admin}/ + app/api/{predict,model-info}/route.ts
│   ├── Dockerfile  package.json  tests/e2e/ (Playwright)
├── k8s/{api-deployment,api-service,frontend-deployment,hpa,aws-iam-role-policy}.yaml
├── infra/aws/                       # CLI scripts or Terraform: ECR, ECS, S3, budget
├── .github/workflows/{ci,deploy,retrain-eval}.yml
├── docker-compose.yml               # api + mlflow + postgres + prometheus + grafana
├── dvc.yaml  params.yaml  requirements.txt  .gitignore
├── docs/{architecture.png,workflow.png,risk-assessment.md,demo-script.md}
├── README.md  REPORT.md
```

---

## 5. Phased Timeline

Assumes ~8 weeks; compress or stretch as needed. Each phase ends with something demonstrable.

### Phase 0 — Setup (week 1)
- [ ] Instructor sign-off on problem and dataset; record licence (CC BY 4.0) in README.
- [ ] GitHub repo, branch protection on `main` (PR + passing CI required), PR template, `.gitignore`.
- [ ] Python env, `requirements.txt` (pin versions), pre-commit (ruff, black, gitleaks).
- [ ] AWS account: **budget alert first**, IAM user/roles, no root keys, enable GitHub OIDC provider.
- [ ] Skeleton repo layout committed.

### Phase 1 — Data, DVC, DagsHub (week 1–2) · S1 + S2
- [ ] `ingestion.py` pulls the dataset into `data/raw`; `validation.py` checks schema, types, nulls, ranges, invalid categories (e.g. `EDUCATION` ∈ {0..6}), duplicates.
- [ ] DVC init; remote = S3 bucket (primary) and DagsHub storage (secondary). `dvc.yaml` stages: `ingest → validate → preprocess → train → evaluate`.
- [ ] **Reproducibility demo:** `git checkout <old commit> && dvc checkout` restores the matching dataset; script it.
- [ ] **Merge-conflict doc:** deliberately create a conflicting `.dvc` file change on two branches, resolve, document steps in `docs/`.
- [ ] Rubric hit: Git/DVC/DagsHub (9 marks).

### Phase 2 — Modelling, MLflow, Feast (week 2–3) · S1
- [ ] Modular `preprocessing.py` (sklearn `ColumnTransformer`, no leakage; train-only fit), engineered features (utilisation ratio, payment delay stats, bill/pay ratios).
- [ ] `train.py`, `tune.py` (Optuna, ≥ 20 trials on 2–3 model families), `evaluate.py` (AUC, PR-AUC, recall/precision at threshold, KS, calibration plot, confusion matrix).
- [ ] MLflow logging per run: algorithm, params, **DVC data hash**, **git commit SHA**, metrics, duration, plots, model artifact. Point tracking URI to DagsHub.
- [ ] Comparison table + written justification of the selected model. Register in MLflow Model Registry (`Staging` → `Production` via aliases).
- [ ] Feast: entity `customer_id`, feature views (payment history, billing, demographics-sans-sensitive), offline retrieval for training, online retrieval for serving, feature-quality validation (freshness/null checks).
- [ ] Fairness: compare AUC/approval rate across `SEX`, age bands; decide whether sensitive attributes are excluded from the model and document it.
- [ ] Rubric hit: MLflow (9) + Feast (6).

### Phase 3 — Tests and CI (week 3–4) · S2
- [ ] pytest suites matching spec §3.5: unit (preprocessing), data (schema), model (shapes, valid probabilities, AUC ≥ threshold), API (valid, malformed, missing fields, wrong types), integration (preprocess → load → predict), **regression** (candidate must not drop > X pts vs. stored baseline metrics).
- [ ] `ci.yml` on push + PR: install, ruff/black, pytest with coverage, bandit, pip-audit, build Docker image, Trivy scan; fails block merge.
- [ ] **Evidence:** keep one PR with a deliberately failing gate (red run) and one passing run; screenshot logs.

### Phase 4 — Serving and containers (week 4) · S3
- [ ] Flask app: `GET /health`, `GET /model-info` (version, run id, metrics, trained-at, data version), `POST /predict` (JSON schema validation with pydantic/marshmallow, 400/422/500 handling, request size limits, no raw PII in logs, returns probability + class + model_version).
- [ ] `/metrics` Prometheus endpoint (request count, error count, latency histogram, prediction-score histogram, model version label).
- [ ] `Dockerfile.api`: slim base, non-root user, gunicorn, no secrets, model pulled from MLflow/S3 at startup or baked in via build arg. `.dockerignore` excludes data.
- [ ] `docker-compose.yml`: api, mlflow, postgres, prometheus, grafana (separate `dev` / `prod` override files).
- [ ] BentoML: build a Bento from the registered model; document how it relates to the Flask API (e.g. Bento as the packaging/runner layer or alternate serving path).
- [ ] Run the container on a second machine (teammate laptop) and record it.
- [ ] **Next.js frontend** (`frontend/`, optional for marks, valuable for the demo; owner S1, S3 reviews):
  - Pages: Score applicant (form, presets, probability gauge, risk band, SHAP top factors, model version), Model & data (`/model-info`), Monitoring (Grafana/Evidently/MLflow links + "simulate drift" button), Admin (champion vs. challenger, promote/rollback via GitHub `workflow_dispatch`).
  - Security: browser only calls Next route handlers; handlers attach the API key from env and forward to Flask. No key in client JS, no CORS needed, no raw inputs logged, "not a real lending decision" disclaimer.
  - Packaging: `frontend/Dockerfile` (multi-stage, standalone output, non-root); added to Compose and K8s. AWS deployment optional (second Fargate service) only if time/cost allow.
  - CI: Node job (lint, typecheck, Playwright smoke test against the API container, Trivy image scan).
  - Fallback if time is short: Swagger/OpenAPI docs on the Flask API and Grafana only.
- [ ] Rubric hit: Containerization and serving (10).

### Phase 5 — Orchestration: Airflow + Kubeflow (week 4–6) · S2 (+S1 for components)
- [ ] Spin up **kind/Minikube** and install Kubeflow Pipelines standalone (not full Kubeflow, to save RAM). Fallback if it is too heavy: KFP local runner plus a documented k8s run for the demo video.
- [ ] KFP components with typed inputs/outputs: `preprocess` → `train_tune` → `evaluate` (threshold gate, logs to MLflow) → `package_register`. Compile to `compiled_pipeline.yaml`.
- [ ] Airflow DAG `ml_orchestrator_dag`: `fetch_data (S3) → validate (fail stops DAG) → dvc_push → trigger_kfp_run → wait/poll → check_gate → notify`. Retries, retry_delay, logging, SLA; schedule weekly + manual trigger.
- [ ] Explain roles in REPORT: Airflow = schedule/data/trigger; KFP = containerised ML DAG. They do not run duplicate workloads.
- [ ] Single trigger runs the whole thing end-to-end, no notebook cells.
- [ ] Rubric hit: Pipelines (12).

### Phase 6 — AWS deployment and CD (week 5–6) · S3 + S2
- [ ] S3 bucket(s) (private, versioned, encrypted): DVC remote, MLflow artifacts, model.
- [ ] ECR repo; ECS Fargate cluster, smallest task (0.25 vCPU / 0.5 GB), 1 task, ALB or public IP restricted by security group to team/instructor IPs. API key header for access control.
- [ ] IAM: task role (read model S3, write CloudWatch only), execution role, GitHub **OIDC** deploy role (push to ECR, update ECS service). Zero static keys in GitHub. Secrets Manager for API key.
- [ ] `deploy.yml`: on `main` after CI → build → scan → push to ECR (tagged by git SHA) → update ECS task definition → smoke test `/health` and `/predict`.
- [ ] **Rollback:** previous task-definition revision / image tag kept; `workflow_dispatch` input to redeploy a given tag. Demo it.
- [ ] CloudWatch log group for the API, plus an alarm (5xx rate or p95 latency) via SNS email.
- [ ] Document services, cost estimate, and teardown script (`infra/aws/teardown.sh`). **Tear down after each demo.**
- [ ] Rubric hit: AWS (12).

### Phase 7 — Kubernetes scaling demo (week 6) · S3
- [ ] `api-deployment.yaml` (readiness + liveness probes on `/health`, resource requests/limits), `api-service.yaml`, optional HPA.
- [ ] kind/Minikube: scale to 3 replicas, `kubectl delete pod` to show self-healing, load-test (hey / locust) to show load balancing across pods.
- [ ] Write limitations: kube-proxy round-robin only, no session affinity, single-node, no real autoscaling without metrics-server. A production EKS cluster is not required.
- [ ] Rubric hit: Kubernetes (6).

### Phase 8 — Monitoring and automated retraining (week 6–7) · S3 + S1 + S2
- [ ] Prometheus scrapes the API; Grafana dashboard: request rate, error rate, p50/p95 latency, health, predictions by model version, score distribution, drift score, data-quality failures, container CPU/mem.
- [ ] Prediction log store (hashed/pseudonymised inputs + score + model_version) to S3/SQLite; labels joined later (UCI has ground truth, so **simulate delayed labels** by replaying held-out months).
- [ ] Evidently job: data-quality + drift (PSI/KS) reference vs. current; emits JSON/HTML report + Prometheus gauge. Performance-when-labels-arrive report.
- [ ] Alerts (Grafana/Prometheus Alertmanager): error rate > 5 % for 5 min, p95 latency > 500 ms, drift share > 30 %. Capture evidence by triggering each on purpose.
- [ ] `retrain_dag`: triggers = weekly schedule **or** drift/performance threshold breached → revalidate data → run KFP → evaluate challenger vs. champion on the same holdout (predefined rule, e.g. AUC +0.5 pt and no fairness regression) → if better, register as Staging and request approval; else discard and log.
- [ ] **Controlled drift scenario:** inject shifted data (e.g. inflated `LIMIT_BAL`, delayed `PAY_*`) → alert fires → investigation steps → retrain → evaluate → decision. Script it for the demo.
- [ ] Promotion/rollback doc: MLflow alias move (`champion`) + redeploy; rollback = repoint alias + redeploy previous tag.
- [ ] Rubric hit: Monitoring (6).

### Phase 9 — Security, ethics, docs (week 7–8) · all
- [ ] Security checklist implemented, not just described: gitleaks in CI, `.env` ignored, least-privilege IAM policy JSON committed, input validation + rate limit, no PII in logs, image non-root, Trivy results.
- [ ] Risk assessment (≥ 3 project-specific risks), e.g.:
  1. Biased decisions against protected groups → exclude/monitor `SEX`/`MARRIAGE`, report group metrics.
  2. Drift from economic shifts → Evidently monitoring + retrain trigger.
  3. Cost overrun on AWS → budget alert, Fargate minimum, teardown script.
  4. Credential leak → OIDC, Secrets Manager, gitleaks.
  5. Model misuse for real lending → disclaimer, model card, human-in-the-loop.
- [ ] Model card: intended use, limitations, fairness findings, explainability (SHAP summary).
- [ ] `README.md` reproduce-from-scratch instructions, tested by a teammate who didn't write it.
- [ ] `REPORT.md`: architecture, decisions, results, automation vs. manual table, risks, individual contribution statement.
- [ ] Architecture + workflow diagrams (draw.io/mermaid export).

### Phase 10 — Dress rehearsal and defense (week 8)
- [ ] Follow `docs/demo-script.md` end to end on a clean machine; time it.
- [ ] Record a backup video of each major step in case AWS or K8s misbehaves live.
- [ ] Each member rehearses explaining another member's component.

---

## 6. Demo Script (spec §6, ordered)

1. Push a branch, open PR → CI runs. Show **a failing PR blocked** (bad test or low-AUC fixture), then fix and go green.
2. Merge → Airflow DAG triggered (UI) → validation passes → KFP run starts.
3. KFP graph: preprocess → train/tune → evaluate → register. Show component outputs feeding later stages.
4. MLflow/DagsHub: runs compared, metrics, DVC version + commit SHA, registry versions.
5. Gate demo: a deliberately weak model is rejected and **not** deployed.
6. CD: image to ECR → ECS deploy → `curl` `/health`, `/model-info`, `/predict` (valid + malformed).
7. K8s: replicas, kill a pod, load balancing.
8. Grafana dashboard live; inject drift → alert fires → Evidently report.
9. Retrain DAG → challenger vs. champion → approval → promote.
10. Rollback to the previous version. Show CloudWatch logs. Tear down AWS.

---

## 7. Spec Traceability

| Spec item | Where covered |
|---|---|
| 3.1 Modular code | `src/` modules, Phase 2 |
| 3.2 Git/DVC/DagsHub | Phase 1 |
| 3.3 MLflow | Phase 2 |
| 3.4 / 3.11 Airflow + Kubeflow | Phase 5 |
| 3.5 Testing | Phase 3 |
| 3.6 CI/CD | Phases 3, 6 |
| 3.7 Docker/Compose | Phase 4 |
| 3.8 Flask API | Phase 4 |
| 3.9 AWS | Phase 6 |
| 3.10 Kubernetes | Phase 7 |
| 3.12 / 3.12A Monitoring, retraining | Phase 8 |
| 3.13 Security, ethics | Phase 9 |
| Rubric: Feast, BentoML, security scanning | Phases 2, 4, 3 |

### Minimum acceptance checklist (spec §9)
- [ ] Reproducible training pipeline
- [ ] Data/code versions identifiable and recoverable
- [ ] Experiments/metrics in MLflow
- [ ] Tests run in GitHub Actions
- [ ] Quality-gate failure prevents deployment
- [ ] Flask API in Docker
- [ ] Deployed on AWS and serves a prediction
- [ ] Kubernetes deployment + scaling demonstrated
- [ ] Airflow + Kubeflow documented and demonstrated
- [ ] Grafana + Prometheus working, one alert shown, retraining triggered, candidate eval + promotion + rollback shown, security implemented
- [ ] Someone else can reproduce from README

---

## 8. Risks to the Schedule

| Risk | Mitigation |
|---|---|
| Kubeflow is heavy on a laptop | KFP standalone on kind; allocate ≥ 8 GB to Docker; record a backup video |
| AWS cost surprises | Budget alert on day one, Fargate 0.25 vCPU, teardown script, no NAT gateway or EKS |
| Too many tools, shallow integration | Build a thin vertical slice (data → train → API → deploy) by end of week 4, then deepen each part |
| Merge conflicts in DVC files | Documented workflow; one owner for `dvc.yaml` |
| Live demo failure | Scripted demo, pre-recorded clips, pre-warmed environments |

## 9. Open Questions for the Instructor
- Is a tabular credit-risk problem acceptable, with Hugging Face limited to model-card hosting, or must a HF model be served?
- Are Feast and BentoML expected deliverables? The rubric scores them, but the requirements body does not mention them.
- Is the spec's "FastAPI" in the repo tree a typo given the Flask requirement?
- Kubeflow on local kind accepted for the pipeline demo?
