# RULES.md — Project Rules

Hard constraints for everyone (humans and AI agents). Items marked **[SPEC]** come straight from the course requirements and are graded. Breaking them can fail the acceptance criteria.

## 1. Security and secrets
- Never commit credentials, keys, tokens, `.env`, kubeconfigs or Terraform state. Commit `.env.example` with placeholders only. **[SPEC]**
- GitHub → AWS auth uses **OIDC roles**, not static access keys. **[SPEC]**
- Runtime secrets (API key, DB password) come from Secrets Manager or env injection, never baked into images. **[SPEC]**
- IAM is least-privilege. No `*:*`; scope to the specific bucket, repo or log group. **[SPEC]**
- Security groups only open the ports needed, restricted to known IPs where possible. **[SPEC]**
- The frontend never receives the API key; it goes through server-side route handlers.
- Run gitleaks, bandit, pip-audit and Trivy in CI. High/critical findings block merge unless documented as accepted.
- If a secret is ever committed, rotate it immediately; deleting the commit is not enough.

## 2. Data, privacy, ethics
- Raw/processed data is tracked by **DVC only**, never git. **[SPEC]**
- Record the dataset source and licence (UCI, CC BY 4.0) in the README. **[SPEC]**
- Do not log raw request payloads. Log request IDs, model version, score and latency only. **[SPEC]**
- Sensitive attributes (`SEX`, `MARRIAGE`, `AGE`) are excluded from model inputs or explicitly justified; report fairness metrics per group. **[SPEC]**
- The model is for coursework only; show a "not a real lending decision" disclaimer in the UI and docs.
- Prediction logs have a retention limit (default 30 days) and must be pseudonymised.

## 3. Code and repository
- Modular Python in `src/`. No pipeline logic in notebooks; notebooks are for exploration only. **[SPEC]**
- Pin dependency versions in `requirements.txt` (and `package-lock.json`).
- Lint and format with ruff and black; type hints on public functions.
- Branches: `feat/…`, `fix/…`, `chore/…`, `docs/…`. Never commit to `main`; use PRs with at least one teammate review. **[SPEC]**
- Commit messages: imperative, specific (`Add schema validation for PAY_* columns`). No "update" or "fix stuff".
- Keep PRs small and focused. One owner per `dvc.yaml` change to avoid `.dvc` merge conflicts; document any conflict resolution. **[SPEC]**
- No large files in git (> 1 MB needs a reason).

## 4. ML and reproducibility
- All randomness is seeded from config. Same data + code + params must give the same metrics.
- Fit transformers on **train only**. Split before feature engineering that uses target or global statistics. No leakage.
- Every MLflow run logs: algorithm, params, DVC data version, git commit SHA, metrics, duration, artifacts/plots. **[SPEC]**
- Model selection is justified in writing from compared runs. **[SPEC]**
- Model versions are managed through the MLflow registry; "Production" is an alias moved only by an approved promotion.

## 5. Quality gates
- **A model that fails its minimum-performance threshold must not proceed to deployment.** **[SPEC]**
- Gate thresholds live in config (initial: ROC-AUC ≥ 0.75; tune after baseline). Never lower a threshold just to make a run pass.
- A candidate replaces the champion only if it beats it on the predefined criteria using the same holdout, with no fairness regression.
- CI blocks merge when tests, lint, security scan or Docker build fails. **[SPEC]**
- Required test categories: unit, data, model, API, integration, regression. **[SPEC]**
- Do not skip, delete or weaken a failing test to get green; fix the cause. Keep one documented failing-gate run as evidence.

## 6. Automation vs. manual approval
- Fully automated: CI, data validation, training, evaluation, image build/push, staging deploy, drift detection, retrain trigger.
- Manual gates: promotion to Production, rollback, and acceptance of a challenger that doesn't clearly win.
- Automation must **never blindly promote** every newly trained model. **[SPEC]**
- The whole ML pipeline must run end to end from one Airflow DAG trigger, with no manual notebook or script steps. **[SPEC]**

## 7. Containers and serving
- Run Flask under gunicorn (production server), not the dev server. **[SPEC]**
- Containers run as non-root, on slim base images, with a `.dockerignore` that excludes data, `.git` and secrets. **[SPEC]**
- Dev and prod configs are separate (Compose override files, env-specific settings). **[SPEC]**
- API contract: `GET /health`, `GET /model-info`, `POST /predict` (plus `/metrics`). Validate inputs, return correct status codes (400/422/500), enforce request size limits. **[SPEC]**
- Tag images by git SHA, never rely on `latest` for deployment. Keep the previous tag for rollback.

## 8. AWS cost and safety **[SPEC]**
- Set the **budget alert before** deploying anything.
- Smallest practical resources: Fargate 0.25 vCPU / 0.5 GB, single task. No NAT gateway, no EKS, no RDS unless approved.
- Document every AWS service used and the steps to reproduce.
- **Tear down billable resources after each demo** (`infra/aws/teardown.sh`) and confirm in the console.
- Never create resources outside the agreed region and naming/tagging scheme (`project=credit-risk`).
- Agents must not provision, modify or delete cloud resources without explicit user confirmation.

## 9. Monitoring and retraining
- Track: request count, error rate, latency, health, model version per prediction, data-quality failures, input drift, and performance when labels arrive. **[SPEC]**
- Maintain at least one working alert with evidence (error rate, latency or drift). **[SPEC]**
- Retraining has a schedule plus at least one defined trigger (drift or performance decline), orchestrated by Airflow. **[SPEC]**
- Drift/label scenarios may be simulated; label them clearly as simulated in docs and the demo.

## 10. Documentation and evidence
- `README.md` must let a teammate who didn't write the code reproduce the main workflow. **[SPEC]**
- Keep evidence as you go: CI logs (pass and fail), MLflow screenshots, Grafana, alert firing, K8s scaling, AWS endpoint, rollback. Store in `docs/evidence/`.
- Maintain a risk assessment with at least three project-specific risks and mitigations. **[SPEC]**
- `REPORT.md` includes an individual contribution statement. Commit history should reflect it.
- Update `memory.md` and `plan.md` checkboxes when work lands.

## 11. For AI agents specifically
- Follow `agents.md`. Ask before: destructive actions, cloud changes, force-pushes, `dvc gc`, dependency upgrades, large refactors, or anything touching `main`.
- Don't invent results, metrics, URLs, resource IDs or file contents. Verify by running or reading.
- Don't add features, services or abstractions the spec and plan don't call for.
- When blocked or when requirements conflict (for example FastAPI vs. Flask in the spec), state the conflict and the assumption you're making, and record it in `memory.md`.
