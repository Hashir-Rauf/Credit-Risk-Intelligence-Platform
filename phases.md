# PHASES.md — Phase-by-Phase Execution Guide

Step-level runbook for `plan.md`. For each phase: **goal, owner, prerequisites, steps, deliverables, exit criteria (definition of done), evidence to capture**. Commands are the intended interface; adjust as the code lands. Tick boxes as you go and log decisions in `memory.md`.

Legend: **S1** Data & ML · **S2** MLOps & Automation · **S3** Deployment & Reliability. Evidence goes to `docs/evidence/phase-N/`.

## Timeline overview

| Phase | Name | Weeks | Lead | Depends on |
|---|---|---|---|---|
| 0 | Setup and governance | 1 | All | none |
| 1 | Data, DVC, DagsHub | 1–2 | S1, S2 | 0 |
| 2 | Modelling, MLflow, Feast | 2–3 | S1 | 1 |
| 3 | Tests and CI | 3–4 | S2 | 1, 2 (parallel) |
| 4 | Serving, containers, frontend | 4–5 | S3, S1 (UI) | 2, 3 |
| 5 | Airflow + Kubeflow orchestration | 4–6 | S2 | 2, 4 |
| 6 | AWS deployment and CD | 5–6 | S3, S2 | 3, 4 |
| 7 | Kubernetes scaling | 6 | S3 | 4 |
| 8 | Monitoring and retraining | 6–7 | S3, S1, S2 | 4, 5, 6 |
| 9 | Security, ethics, docs | 7–8 | All | all |
| 10 | Rehearsal and defense | 8 | All | all |

**Milestone M1 (end of week 4): thin vertical slice.** Data → train → registered model → Flask API in Docker → CI green. Do not start deep work on Phases 7–8 before M1 is demonstrable.

---

## Phase 0 — Setup and governance
**Goal:** a clean, safe foundation: repo, branch rules, environments, AWS guardrails. **Owner:** all. **Prereq:** instructor sign-off.

**Steps**
1. Get instructor approval for the problem, dataset and open questions in `plan.md` §9. Record the answers in `memory.md`.
2. S2: `git init`, create the GitHub repo, push the initial commit (planning docs + `.gitignore`).
3. S2: protect `main` (require PR, 1 review, passing status checks, no force-push). Add a PR template and CODEOWNERS (S1 `src/data src/features src/models`, S2 `pipelines .github tests`, S3 `src/serving docker k8s infra monitoring`).
4. All: create `.venv`, then `requirements.txt` (pinned) and `requirements-dev.txt`. Install pre-commit with ruff, black, gitleaks and a large-file check.
5. S2: add a `Makefile` with `setup`, `lint`, `test`, `up`, `down`, `repro`.
6. S3: AWS account hygiene:
   - Enable MFA and stop using the root user.
   - **Create the budget alert first** (for example $10 and $20 thresholds, email to all three).
   - Create the S3 bucket(s) (private, versioned, encrypted, tagged `project=credit-risk`).
   - Register the GitHub OIDC identity provider.
   - Create the deploy role, scoped to this repo's `main` branch, with no policies attached yet.
7. S1: create the DagsHub project, connected to the GitHub repo.
8. Create the empty skeleton from `plan.md` §4 with `.gitkeep` files, `.env.example`, and a README stub.

**Deliverables:** GitHub repo with protections; AWS budget; S3 bucket; OIDC provider; DagsHub project; skeleton merged via PR.
**Exit criteria**
- [ ] Direct push to `main` is rejected.
- [ ] Pre-commit blocks a test commit containing a fake secret.
- [ ] Budget alert email received (use the test notification).
- [ ] Each member has cloned and run `make setup` successfully.

**Evidence:** screenshot of branch protection, budget page, blocked secret commit.

---

## Phase 1 — Data, DVC, DagsHub
**Goal:** versioned, validated data, and a proven way to recover any past data/code pair. **Owners:** S1 (data), S2 (DVC/Git). **Prereq:** Phase 0.

**Steps**
1. S1: `src/data/ingestion.py` downloads UCI data into `data/raw/` (with checksum verification, idempotent). Document the licence in the README.
2. S1: exploratory notebook in `notebooks/` (not used by the pipeline): class balance, value ranges, odd categories (`EDUCATION` 0/5/6, `MARRIAGE` 0), the `PAY_*` coding.
3. S1: `src/data/validation.py` (Pandera or Great Expectations) with the checks: schema and dtypes, non-null, ranges (`AGE`, `LIMIT_BAL`), allowed categorical sets, duplicate IDs, target in {0,1}. Return a structured report and raise on failure.
4. S2: `dvc init`; remotes: `s3` (default) and `dagshub`. Keep credentials in `.dvc/config.local` or env vars, never in the committed config.
5. S2: `dvc.yaml` stages `ingest → validate → preprocess → train → evaluate` with `params.yaml` and metrics/plots outputs (stub downstream stages at first).
6. S2: `dvc add data/raw` (or make it a stage output), commit `.dvc`/`dvc.lock`, `dvc push` to both remotes.
7. S2 + S1: **Reproducibility demo script** `scripts/repro_demo.sh`:
   `git checkout <old-sha> && dvc checkout` then verify the data hash matches the recorded one, then `git checkout main && dvc checkout`.
8. S2: **Merge-conflict exercise:** two branches modify the data/`dvc.lock`; merge, resolve (take both, re-run `dvc repro`, commit), and write the procedure in `docs/dvc-merge-conflicts.md`.
9. S1: add data tests (`tests/test_data.py`), including a deliberately corrupt fixture that must fail validation.

**Deliverables:** ingestion + validation modules, DVC pipeline, two remotes, repro script, merge-conflict doc.
**Exit criteria**
- [ ] `dvc repro` runs from a fresh clone after `dvc pull`.
- [ ] Repro demo restores the earlier data byte-for-byte (hashes match).
- [ ] Corrupt data fails validation with a clear message.
- [ ] No data file is tracked by git.

**Evidence:** terminal log of the repro demo, `dvc dag` output, DagsHub data view, conflict-resolution write-up.

---

## Phase 2 — Modelling, MLflow, Feast
**Goal:** a justified model, tracked experiments, and a feature store. **Owner:** S1. **Prereq:** Phase 1.

**Steps**
1. `src/features/preprocessing.py`: sklearn `ColumnTransformer` (scale numeric, encode categoricals), fit on train only; save the fitted transformer with the model as one `Pipeline`.
2. Engineered features: credit utilisation (`BILL_AMT/LIMIT_BAL`), payment ratio (`PAY_AMT/BILL_AMT`), max/mean delay, count of delinquent months. Unit-test each.
3. Stratified split (train/val/test) with fixed seed from `params.yaml`; **test set untouched until the final evaluation**.
4. `src/models/train.py`: baseline logistic regression, random forest, XGBoost/LightGBM, with class-imbalance handling (class weights or `scale_pos_weight`).
5. `src/models/tune.py`: Optuna, ≥ 20 trials per family, cross-validated on train, with the objective on val AUC. Log trials as nested MLflow runs.
6. `src/models/evaluate.py`: ROC-AUC, PR-AUC, KS, recall/precision at the chosen threshold, confusion matrix, calibration plot; the gate check reads thresholds from `configs/`.
7. MLflow logging helper (single module used everywhere): algorithm, params, DVC data hash, git SHA, metrics, duration, plots, model. Tracking URI set to DagsHub (via env).
8. Compare runs in the MLflow UI; write `docs/model-selection.md` justifying the winner (metric table plus trade-offs).
9. Register the winner in the MLflow registry; set alias `staging`; `champion` is set only after the Phase 6 deployment.
10. Fairness: AUC, approval rate and false-positive rate by `SEX` and age band. Decide on excluding sensitive features, then document the decision (`docs/fairness.md`). Add SHAP summary for the model card.
11. Feast: `feature_repo/` with entity `customer_id`, feature views (payment history, billing/utilisation, demographics excluding sensitive), offline store = parquet, online store = SQLite; `feast apply`, `get_historical_features` for training, `materialize` + `get_online_features` for serving; add feature quality checks (null rate, freshness) as tests.

**Deliverables:** modular training code, tuning, evaluation, tracked runs, registry entry, selection doc, fairness doc, Feast repo.
**Exit criteria**
- [ ] `dvc repro` trains and logs a run with all required fields.
- [ ] ≥ 3 model families compared; selection justified in writing.
- [ ] Winner meets the gate (AUC ≥ 0.75 or documented adjusted threshold).
- [ ] Same seed gives the same metrics on re-run.
- [ ] Online feature fetch returns the same values as offline for a sample customer.

**Evidence:** MLflow comparison screenshot, registry page, Feast `apply`/`get_online_features` output, fairness table.

---

## Phase 3 — Automated tests and CI
**Goal:** every PR is tested, scanned and gated. **Owner:** S2. **Prereq:** Phase 1 (start in parallel with 2).

**Steps**
1. `tests/` by category: unit (preprocessing, feature functions, threshold logic), data (schema, nulls, types, invalid records), model (output shape, probabilities in [0,1], AUC ≥ threshold on a small fixed sample), API (added in Phase 4), integration (preprocess → load → predict), regression (candidate vs. stored `baseline_metrics.json`).
2. Small deterministic fixtures in `tests/fixtures/` (a few hundred rows) so CI doesn't need the full dataset or DVC.
3. `.github/workflows/ci.yml`, triggered on `push` and `pull_request`:
   - setup Python with pip cache → install
   - ruff, black --check
   - pytest with coverage (fail under a set minimum, e.g. 70 %)
   - bandit, pip-audit, gitleaks
   - build the Docker image (Phase 4 adds the Dockerfile) and Trivy-scan it
4. Mark the jobs as **required status checks** on `main`.
5. Create the evidence PRs: one with a deliberately failing test or sub-threshold model fixture (red), then the fix (green). Keep both runs' logs.
6. Add a coverage badge and CI badge to the README.

**Deliverables:** test suite, `ci.yml`, required checks, red and green runs.
**Exit criteria**
- [ ] A failing test or lint error blocks the merge button.
- [ ] CI completes in < 10 minutes.
- [ ] A bad-model fixture fails the model test, proving the gate works.

**Evidence:** red run + green run links/screenshots, branch-protection check list.

---

## Phase 4 — Serving, containers, frontend
**Goal:** the model behind a validated REST API, containerised, with a UI. **Owners:** S3 (API/Docker/BentoML), S1 (frontend). **Prereq:** Phases 2, 3. **Milestone M1 is reached here.**

**Steps (API, S3)**
1. `src/serving/app.py` (Flask app factory), `schemas.py` (request validation), `model_loader.py` (load the registered model by alias from MLflow/S3 once at startup).
2. Endpoints: `GET /health` (liveness plus model-loaded flag), `GET /model-info` (version, run ID, metrics, trained-at, data version, git SHA), `POST /predict` (single and batch; returns probability, label, threshold, model_version, request_id).
3. Error handling: 400 malformed JSON, 422 schema/range errors, 413 oversize, 500 safe message with no stack trace; API-key check via header.
4. `src/monitoring/metrics.py`: Prometheus counters/histograms and `/metrics`. Structured JSON logs with no raw inputs.
5. `tests/test_api.py`: valid, missing field, wrong type, out-of-range, empty body, oversize, bad key, `/model-info` contents.
6. `docker/Dockerfile.api`: slim base, multi-stage, non-root, gunicorn, `HEALTHCHECK`, no secrets/data. `.dockerignore`.
7. `docker-compose.yml` + `docker-compose.dev.yml` / `docker-compose.prod.yml`: api, mlflow, postgres, prometheus, grafana.
8. BentoML: `bentofile.yaml`, build a Bento from the registered model; document its role (packaging/alt serving path) and run it once.
9. **Cross-machine test:** teammate runs `docker compose up` on another laptop from the README only; record the result.

**Steps (frontend, S1; start once the API schema is frozen)**
10. Next.js app (App Router, TypeScript), `app/api/predict` and `app/api/model-info` route handlers adding the API key server-side.
11. Pages: Score applicant (form, presets, gauge, risk band, SHAP factors), Model & data, Monitoring links, Admin (champion vs. challenger, promote/rollback trigger).
12. `frontend/Dockerfile` (standalone output, non-root); add to Compose; Playwright smoke test; add the Node job to CI.

**Deliverables:** Flask API, tests, Dockerfile, Compose stack, Bento, Next.js UI.
**Exit criteria**
- [ ] `docker compose up` brings up the full stack; `curl` examples in the README work.
- [ ] All API tests pass in CI; image passes Trivy (no unaccepted high/critical).
- [ ] Verified on a second machine.
- [ ] UI scores a customer end to end; the API key is not visible in the browser dev tools.

**Evidence:** curl transcript (valid and malformed), `/model-info` JSON, second-machine recording, UI screenshots.

---

## Phase 5 — Orchestration: Airflow + Kubeflow Pipelines
**Goal:** one trigger runs the whole ML pipeline end to end. **Owner:** S2 (S1 supports the components). **Prereq:** Phases 2, 4.

**Steps**
1. Create the cluster: `kind create cluster` (config with enough memory; Docker Desktop ≥ 8 GB). Install **KFP standalone** (not full Kubeflow). Port-forward the UI. Write the exact steps in `docs/kubeflow-setup.md`.
2. `docker/Dockerfile.ml`: base image for the KFP components (project code installed, pinned deps). Load it into kind (`kind load docker-image`) or push to ECR/registry.
3. Components in `pipelines/kubeflow/components/` with typed inputs/outputs (`Dataset`, `Model`, `Metrics` artifacts):
   - **A** `preprocess`: feature engineering and the train/val/test split (Feast offline retrieval)
   - **B** `train_tune`: training and Optuna tuning in its own pod
   - **C** `evaluate`: metrics, the threshold gate (fails the pipeline or emits `passed=false`), logs to MLflow
   - **D** `package_register`: builds the model package/container, registers it in MLflow (alias `staging`)
4. `pipeline_definition.py` wires them with conditional execution (D only if C passed); compile to `compiled_pipeline.yaml`; commit it.
5. Airflow: run via Docker Compose or `airflow standalone` (lightweight). DAG `ml_orchestrator_dag`:
   `fetch_data (S3/UCI) → validate → dvc_push → trigger_kfp_run (KFP client/API) → poll_run → check_gate → notify`
   - retries (2–3), `retry_delay`, timeouts, `on_failure_callback`, logging, an SLA
   - schedule weekly plus manual trigger; connection and credentials via Airflow Connections/env, not code
6. Validation failure must stop the DAG before KFP starts (test it with the corrupt data fixture).
7. Write `docs/orchestration.md`: the roles of Airflow vs. KFP, how component outputs feed later steps, and why the workloads don't duplicate.
8. Dry-run end to end from a single DAG trigger; fix flakiness before recording.

**Deliverables:** Airflow DAG, KFP components and compiled pipeline, setup and design docs.
**Exit criteria**
- [ ] One DAG trigger → validated data → KFP run → evaluated, registered model, with zero manual steps.
- [ ] Corrupt data stops the DAG at validation.
- [ ] A sub-threshold model is not registered/promoted.
- [ ] Retry behaviour demonstrated (kill a task once).

**Evidence:** Airflow graph + logs, KFP run graph with artifacts, MLflow run created by the pipeline, a failed-gate run.

---

## Phase 6 — AWS deployment and CD
**Goal:** approved image runs on AWS and is reachable, deployed by CI/CD with rollback. **Owners:** S3 (infra), S2 (workflow). **Prereq:** Phases 3, 4.

**Steps**
1. `infra/aws/` scripts (AWS CLI or Terraform) for: ECR repo, S3 buckets (reuse Phase 0), CloudWatch log group (retention 14–30 days), ECS cluster, task definition (Fargate 0.25 vCPU / 0.5 GB), service (1 task), security group (inbound only the needed port from allowed IPs; use an ALB only if required).
2. IAM (committed as JSON under `k8s/aws-iam-role-policy.yaml` / `infra/aws/iam/`):
   - **Task role:** read the model/artifact prefix in S3, write the log group.
   - **Execution role:** pull from ECR, write logs, read the one secret.
   - **GitHub OIDC deploy role:** ECR push to this repo, `ecs:UpdateService`/`RegisterTaskDefinition`, `iam:PassRole` for the two roles only; trust restricted to this repo and `main`.
3. Secrets Manager: store the API key; reference it from the task definition.
4. `.github/workflows/deploy.yml` (on merge to `main` after CI success): assume the OIDC role → build → Trivy → push to ECR tagged with the git SHA → render the task definition with the new image → deploy → wait for stable → smoke test `/health` and `/predict`; fail and roll back on a failed smoke test.
5. **Environments:** use a GitHub Environment `production` with a required reviewer to make promotion a manual gate.
6. **Rollback:** keep the previous task-definition revision; add a `workflow_dispatch` workflow taking an image tag or revision; rehearse it.
7. CloudWatch alarm (5xx count or p95 latency) → SNS email. Trigger it on purpose once.
8. `docs/aws-deployment.md`: services used, ordered reproduction steps, access control, estimated cost; `infra/aws/teardown.sh` removes the ECS service, cluster, ALB, ECR images, etc. Run it and verify in the console.

**Deliverables:** running ECS service, `deploy.yml`, rollback workflow, IAM policies, cost/teardown docs.
**Exit criteria**
- [ ] Merge to `main` deploys with no manual AWS clicks and no stored AWS keys.
- [ ] `curl` to the public endpoint returns a valid prediction; requests without the API key are rejected.
- [ ] Failed quality gate in CI = no deployment (shown).
- [ ] Rollback to the previous version demonstrated.
- [ ] Teardown leaves no billable resources.

**Evidence:** deploy run logs, ECR image list with SHA tags, ECS service page, CloudWatch logs and alarm, rollback run, post-teardown console screenshot, Cost Explorer.

---

## Phase 7 — Kubernetes deployment and scaling
**Goal:** show orchestration, self-healing and scaling locally. **Owner:** S3. **Prereq:** Phase 4 (can run alongside Phase 6).

**Steps**
1. Use the Phase 5 kind cluster (or Minikube); load the API image.
2. `k8s/api-deployment.yaml`: image tag by SHA, replicas 2, **readiness and liveness probes** on `/health`, resource requests/limits, env from ConfigMap/Secret, rolling-update strategy.
3. `k8s/api-service.yaml` (ClusterIP/NodePort) and optional `hpa.yaml` (needs metrics-server). Add `frontend-deployment.yaml`.
4. Demo script `scripts/k8s_demo.sh`:
   - `kubectl scale deploy/api --replicas=3` → show pods
   - `kubectl delete pod <name>` → show it replaced
   - break the readiness probe (bad env) → show the pod stays out of the Service
   - load test (`hey`/`locust`) → show requests spread across pods (log pod name or the `/metrics` counters)
   - rolling update to a new image tag, then `kubectl rollout undo`
5. `docs/kubernetes.md`: how Service load balancing works (kube-proxy, round-robin-ish), and its limitations (no session affinity by default, single node, no cluster autoscaling, local only).

**Deliverables:** manifests, demo script, doc.
**Exit criteria**
- [ ] Multiple replicas serving traffic; failed pod automatically replaced.
- [ ] Probes demonstrably affect routing.
- [ ] Rolling update and rollback work.

**Evidence:** `kubectl get pods -w` recording, load-test output, probe failure events.

---

## Phase 8 — Monitoring, drift, and automated retraining
**Goal:** observe the live service, detect drift, and run a controlled retrain-evaluate-promote loop. **Owners:** S3 (metrics/dashboards/alerts), S1 (Evidently/evaluation logic), S2 (retrain DAG). **Prereq:** Phases 4, 5, 6.

**Steps**
1. **Prometheus:** `monitoring/prometheus.yml` scraping the API (Compose and K8s); `alert_rules.yml`.
2. **Grafana:** provisioned dashboard JSON (committed) with: request rate, error rate, p50/p95/p99 latency, health/up, predictions by model version, score distribution, drift score, data-quality failures, container CPU/memory.
3. **Alerts:** (a) error rate > 5 % for 5 min, (b) p95 latency > 500 ms, (c) drift share > 30 %. Route to email/Slack webhook via Alertmanager or Grafana alerting. Each is deliberately triggered once with evidence.
4. **Prediction log:** the API appends pseudonymised records (request ID, hashed customer ID, features needed for drift, score, model version, timestamp) to S3/SQLite, with retention.
5. **Ground truth:** simulate delayed labels by replaying held-out months; a job joins labels to predictions and computes live AUC/recall by window. Label this as simulated.
6. **Evidently job** (`src/monitoring/drift.py`): data-quality + data-drift reports (reference = training data vs. recent predictions), HTML to S3, plus numeric drift metrics exposed to Prometheus; performance report when labels exist.
7. **Retrain DAG** `retrain_dag`: triggers = weekly schedule or a drift/performance threshold breach (sensor on the drift metric) → revalidate data → trigger the KFP pipeline → pull challenger and champion metrics → **decision step** using predefined rules (AUC +0.5 pt on the same holdout, no fairness regression, passes gate) → register as `challenger` and request approval (GitHub Environment or Admin page).
8. **Promotion and rollback:** approval moves the MLflow alias `champion` and runs `deploy.yml` with the new model version; rollback repoints the alias and redeploys the prior tag. Document both in `docs/promotion-rollback.md`.
9. **Controlled drift scenario** `scripts/simulate_drift.py`: send shifted data (inflated `LIMIT_BAL`, worsened `PAY_*`) → dashboard shifts → drift alert fires → investigation checklist (Evidently report, data-quality, upstream change?) → retrain DAG → evaluation → decision → promote or reject.
10. CloudWatch: confirm ECS logs and infra metrics are collected; add a Logs Insights saved query for errors.

**Deliverables:** Prometheus/Grafana configs, dashboards, alert rules, Evidently job, retrain DAG, promotion/rollback doc, drift script.
**Exit criteria**
- [ ] Dashboard shows live metrics, including the model version per prediction.
- [ ] At least one alert fires and resolves, with evidence.
- [ ] Drift simulation triggers retraining automatically; a challenger is evaluated against the champion; the decision is logged.
- [ ] A worse challenger is rejected (demonstrate); a better one is promoted only after approval.
- [ ] Rollback works.

**Evidence:** Grafana screenshots (normal, degraded, alert state), alert notification, Evidently report, retrain DAG run, MLflow champion/challenger comparison.

---

## Phase 9 — Security, ethics, and documentation
**Goal:** security implemented (not just described), and a complete, reproducible write-up. **Owner:** all. **Prereq:** Phases 1–8 mostly done.

**Steps**
1. **Security audit:** walk through `rules.md` §1–2 and tick each item with a proof link; fix gaps (IAM wildcards, open ports, root containers, secrets in logs, rate limiting, request size limits).
2. Review scanner output (bandit, pip-audit, Trivy, gitleaks); fix or record accepted risks in `docs/security.md`.
3. `docs/risk-assessment.md`: ≥ 3 project-specific risks (bias, drift, cost, credential leak, misuse), each with likelihood, impact, mitigation and the evidence it is implemented.
4. **Model card** (`docs/model-card.md`): intended use, data, metrics, fairness findings, SHAP, limitations, ethical considerations. Optionally publish to Hugging Face Hub if approved.
5. Diagrams (draw.io/mermaid → PNG in `docs/`): architecture and workflow.
6. **README:** prerequisites, setup, `dvc pull`, `dvc repro`, run stack, run tests, deploy, teardown, and troubleshooting. A teammate who didn't write each section runs it from scratch on a clean machine and files fixes.
7. `REPORT.md`: problem, data, architecture, decisions and trade-offs, results, automation vs. manual table, monitoring and retraining, risks, lessons learned, and an individual contribution statement.
8. Check the deliverables list (spec §7) and acceptance criteria (spec §9) against `docs/evidence/`.

**Exit criteria**
- [ ] Every item in the `plan.md` §7 checklist is ticked with linked evidence.
- [ ] A fresh-machine reproduction by a non-author succeeds from the README alone.
- [ ] No secrets in history (`gitleaks detect` on the full history).

---

## Phase 10 — Dress rehearsal and defense
**Goal:** a smooth, timed live demo and a team that can explain everything. **Owner:** all.

**Steps**
1. Freeze features by the start of week 8; only bug fixes after that.
2. Finalise `docs/demo-script.md` following `plan.md` §6 and spec §6 (steps 1–9), with timings and exact commands, plus a table distinguishing fully automated steps from manual approvals.
3. **Pre-warm** before the demo: start the AWS service, kind cluster, Compose stack and Airflow; confirm the budget; check that tokens haven't expired.
4. Run the full demo end to end at least twice, once on a clean machine, and fix anything that was flaky.
5. Record a backup video for each major segment (CI gate, pipeline run, AWS deploy and rollback, K8s scaling, drift → retrain).
6. Q&A drill: each member explains another member's component (use a rotating question list: "why Airflow and KFP both?", "how do you recover old data?", "what blocks a bad model?", "what does rollback do?", "what are the fairness limits?").
7. After the demo: run `infra/aws/teardown.sh`, stop local services, confirm no billable resources, and record the final cost in `REPORT.md`.

**Exit criteria**
- [ ] Demo runs within the allotted time with no manual fixes.
- [ ] Backup videos exist and play.
- [ ] Every member can answer questions on any component.
- [ ] AWS is torn down and the bill is checked.

---

## Cross-phase checklists

**Definition of done for any task:** code merged via reviewed PR · tests added and green · config not hard-coded · docs/README updated · evidence captured · `memory.md` updated.

**Weekly rhythm:** Monday sync (blockers, plan the week) · mid-week integration check (pull `main`, run `make repro test`) · Friday PR review sweep and a `memory.md` update.

**Risk watch (see `plan.md` §8):** Kubeflow memory pressure (allocate resources early; keep the backup video) · AWS cost (check Cost Explorer weekly) · scope creep on the frontend (timebox to ~4 days) · `.dvc` merge conflicts (single owner of `dvc.yaml`).
