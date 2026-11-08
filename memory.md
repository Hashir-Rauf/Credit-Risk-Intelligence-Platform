# MEMORY.md — Project State and Decisions

Living log for the team and AI agents. Keep it short and current. Newest entries at the top of each section. Facts only; no secrets.

_Last updated: 2026-11-08_

## START HERE (for a new session)
1. Read `agents.md`, `rules.md`, then this file. The roadmap is `plan.md`; step-level runbook is `phases.md` (Phase 0 is in progress).
2. You are probably in a **fresh session started after the AWS Agent Toolkit install**, so the `aws-mcp` server and the 25 AWS skills should now be loaded. Check that the AWS MCP tools respond. If not, see "AWS setup" below.
3. `CLAUDE.md` contains the AWS "new experience" rules. It says to **ask the user their help level (low / medium / high) while building**. Ask once when AWS work starts, then record the answer under "Preferences".
4. Never add Claude/Co-Authored-By attribution to commits or PRs (see `rules.md` §11). Do not commit or push unless the user asks.
5. Continue Phase 0 using "Next up" below.

## Current status
- **Phase:** 0 — Setup (in progress). Planning docs done; instructor approved the problem on 2026-11-08.
- **Git:** repo exists, `origin` = `https://github.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform.git`, branch `main`. **Nothing from Phase 0 is committed yet.** Branch protection is not confirmed enabled.
- **Files present:** `plan.md`, `phases.md`, `agents.md`, `rules.md`, `memory.md`, `CLAUDE.md` (AWS rules, delimited block), `.gitignore`, `README.md` (stub), `Makefile`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt` (ranges, not pinned), `.env.example`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`, the spec PDF.
- **Missing (Phase 0, mine to do):**
  - `.pre-commit-config.yaml` (writes failed earlier due to a tool error; content: pre-commit-hooks, ruff, black, gitleaks, detect-private-key, large-file check at 1 MB).
  - Skeleton folders with `.gitkeep` per `plan.md` §4 (`src/{data,features,models,serving,monitoring}`, `tests`, `pipelines/{airflow/dags,kubeflow/components}`, `configs`, `docker`, `k8s`, `monitoring`, `infra/aws`, `docs`, `feature_repo`, `data/{raw,processed}`, `frontend`, `.github/workflows`).
  - First commit on a branch `chore/phase-0-setup`, then a PR to `main`.

## Update 2026-11-08 (later)
- `.pre-commit-config.yaml` and skeleton folders (with `.gitkeep`) are written, not committed.
- User confirmed: AWS budget created; branch protection enabled on `main`; third teammate will not touch code (no CODEOWNERS entry needed); DagsHub project exists at https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform (AI to init/manage the MLflow/DVC wiring; needs a DagsHub token in local `.env`).
- Professor approved: Hugging Face, BentoML, Feast, Flask API, Kubeflow.
- Still unconfirmed: AWS spend limit itself (budget alerts exist).

## Phase 1 UPDATE (later 2026-11-08): all [H] items DONE and committed on `feat/phase-1-data-dvc` (authored Hashir Rauf <hashirpriv04@gmail.com>, no AI attribution). Data versions: v1 30,000 rows md5 a7e80c90..; v2 (`835412d`, dedupe) 29,965 rows md5 22e7b58a..; merged-conflict demo 29,620 rows. Repro demo and merge-conflict exercise run; evidence in `docs/evidence/phase-1/` (README index). Found and fixed: `.gitignore` `models/` rule was hiding `src/models/` (now `/models/`); `*.log` ignored evidence (now excepted). Open: DagsHub data-view screenshot (manual; DagsHub repo is standalone, not a GitHub mirror), PR must be opened by the user (no `gh`), JA-Codex reviews. Account ID scrubbed from memory.md (public repo) but `.dvc/config` still contains the bucket name.

## Phase 1 progress (2026-11-08, earlier notes)
Done: `params.yaml`, `src/data/ingestion.py` (pinned zip SHA-256, deterministic CSV, columns renamed `customer_id`, `PAY_1`, `default`), `src/data/validation.py` (pandera, structured report, raises), `tests/test_data.py` (20 pass) + fixtures (`credit_sample.csv`, `credit_corrupt.csv`), `dvc init`, remotes `s3` (default) and `dagshub` (creds in gitignored `.dvc/config.local`), `dvc.yaml` (ingest -> validate), `dvc push` to both remotes, `scripts/repro_demo.sh`, README data section. Fresh-copy `dvc pull` verified (same MD5). `.venv` created locally (not committed).
Data facts: 30,000 rows, default rate 22.12%, no nulls/dupe IDs; `EDUCATION` has 0/5/6 and `MARRIAGE` 0 (allowed, handle in preprocessing); 35 duplicate rows excluding ID (kept).
Remaining Phase 1: EDA notebook (`notebooks/`), run `scripts/repro_demo.sh` once there are 2 data commits, merge-conflict exercise + `docs/dvc-merge-conflicts.md`, evidence in `docs/evidence/phase-1/`, commit + PR. DVC needs `botocore[crt]` for `aws login` creds (added to requirements).
Note: committed `.dvc/config` contains the S3 bucket name (includes the AWS account ID, which AWS does not treat as secret). User decides whether that is OK for a public repo.

## Ownership rule (user, 2026-11-08)
- Everything the AI does is attributed to **Hashir-Rauf** (CODEOWNERS and commits). Repo-local git identity set to `Hashir Rauf <hashirpriv04@gmail.com>`; the global git email is `jahangir.saleem.mughal@gmail.com` (looks like JA-Codex's), so earlier commits `7dcdc7a..f7b21fb` carry that email. User should confirm `hashirpriv04@gmail.com` is verified on their GitHub account.
- JA-Codex's work (Phase 2 modelling, features, Feast, notebooks, model/fairness docs) is NOT implemented by the AI. Placeholders live in `src/features/preprocessing.py`, `src/models/{train,tune,evaluate}.py`; the flag/to-do list is `docs/HANDOFF-JA-Codex.md`. CODEOWNERS updated: `src/data`, `dvc.yaml`, `.dvc/config`, `params.yaml`, `scripts/`, `tests/test_data.py`, `tests/fixtures/` -> Hashir-Rauf; `src/features`, `src/models`, `feature_repo`, `notebooks`, other `tests/` -> JA-Codex.
- Still the AI's (S2/S3 = Hashir): pipelines, CI, serving, docker, k8s, infra, monitoring, frontend.

## Next up
**Mine (AI):**
1. ~~Write `.pre-commit-config.yaml` and the skeleton folders.~~ Done.
2. Commit on `chore/phase-0-setup` (only when the user asks) and prepare the PR. Branch protection is on, so `main` needs a PR.
3. After the user confirms the AWS spend limit/budget is set: create the S3 bucket (private, versioned, encrypted, tag `project=credit-risk`) and the GitHub OIDC provider + deploy role in **ap-southeast-2** (trust policy restricted to `repo:Hashir-Rauf/Credit-Risk-Intelligence-Platform:ref:refs/heads/main`; no permissions attached until Phase 6). Ask before creating any cloud resource.
4. Then start Phase 1 (ingestion, validation, DVC) once the Phase 0 PR is merged.

**The user's (manual, accounts/settings):**
1. Restart Claude Code in this folder (done if you are reading this in the new session).
2. **AWS spend protection first:** in AWS Settings → Billing set a spend limit; in the Billing and Cost Management console create a budget with email alerts at 50%, 80%, 100%. Do this before any resource is created.
3. Enable branch protection on `main` (PR + 1 review; add required checks after Phase 3). Needs a public repo or a paid GitHub plan.
4. Invite the third teammate; add their handle to CODEOWNERS (currently only `@JA-Codex` and `@Hashir-Rauf`).
5. Create the DagsHub project from the GitHub repo; put the MLflow URI and token in local `.env` (never committed).
6. Ask the professor the remaining questions (see "Assumptions to confirm").

## AWS setup (done 2026-11-08)
- AWS CLI 2.39.6 installed per-user at `C:\Users\Hashir Rauf\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe`. It was **not on PATH in the old terminal**, so use the full path or open a new terminal.
- CLI profile: **`credit-risk`**, region **ap-southeast-2**, signed in with `aws login` (role `AccountFullAccessRole`). Credentials last 12 h, renewable for 90 days; re-run `aws login --region ap-southeast-2 --profile credit-risk` if expired.
- Account type: the **new AWS experience** ("project"; managed IAM/SCPs; single region; spend limit and billing in AWS Settings; budgets in the Billing console). Use "project", "team member", "AWS Settings", "selected Region" in wording. Resources only in ap-southeast-2 (WAF/CloudWatch Logs in us-east-1 only when needed). No StackSets, Lambda@Edge or cross-region replication.
- Agent Toolkit installed (region us-east-1 for the toolkit service only): 25 skills in `~/.claude/skills` (also Codex, Cursor), and the `aws-mcp` server added to `~/.claude.json`, `~/.cursor/mcp.json`, `~/.gemini/settings.json` with `AWS_MCP_PROXY_PROFILES=credit-risk`. Codex MCP not configured (no `codex` on PATH).
- For any AWS task, first search the toolkit docs (`aws___search_documentation`, topic `agent_skills`) and load the matching skill (`aws___retrieve_skill`), per `CLAUDE.md`.
- If the MCP server fails with `JSON-RPC error: -32602`, the profile env var is missing or credentials expired.
- Security note: `~/.claude.json` holds a plaintext API key for the `magic` MCP server (not authenticating). The user should rotate it; never print or commit that file.
- `aws agent-toolkit list-available-skills` may print a charmap/Unicode error on Windows consoles; the data still returns.

## Decisions log
| Date | Decision | Why |
|---|---|---|
| 2026-11-08 | Instructor approved the credit-risk (tabular classification) problem | Confirmed by the user. |
| 2026-11-08 | AWS region **ap-southeast-2**, CLI profile `credit-risk`, new AWS experience | Set by the account/project. |
| 2026-11-05 | Problem: credit-card default prediction on UCI "Default of Credit Card Clients" (30k rows, CC BY 4.0) | Small and cheap to train in CI; clear licence; has an ID for Feast and sensitive attributes for fairness work. |
| 2026-11-05 | Serve with **Flask** + gunicorn, not FastAPI | Spec text mandates Flask; the spec's repo tree says FastAPI (treated as a typo). |
| 2026-11-05 | Orchestration: Airflow → Kubeflow Pipelines on local kind/Minikube | Spec requires dual orchestration; local K8s avoids EKS cost. |
| 2026-11-05 | AWS compute: ECS Fargate (0.25 vCPU / 0.5 GB), no EKS/NAT | Cost control. K8s scaling is demonstrated locally. |
| 2026-11-05 | Frontend: Next.js with server-side proxy to Flask | Better demo; API key never reaches the browser. Not graded, so it is time-boxed with a Swagger fallback. |
| 2026-11-05 | Added Feast, BentoML, security scanning | Rubric scores them though the spec body doesn't mention them. |
| 2026-11-05 | Hugging Face limited to model-card hosting (pending approval) | Tabular problem; HF is "where appropriate". |

## Assumptions to confirm
- [x] Instructor accepts the tabular credit-risk problem (confirmed 2026-11-08).
- [x] Hugging Face acceptable (confirmed 2026-11-08).
- [x] Feast and BentoML approved (confirmed 2026-11-08).
- [x] Flask API approved (confirmed 2026-11-08).
- [x] Kubeflow approved (confirmed 2026-11-08); local kind setup still assumed.
- [ ] Initial quality gate ROC-AUC ≥ 0.75 is reasonable; revisit after the baseline.

## AWS findings (2026-11-08)
- Account ID <account-id> (role `AccountFullAccessRole`), no S3 buckets yet.
- **SCP explicitly denies `iam:ListOpenIDConnectProviders`** (policy p-tjsq4aou). Creating the GitHub OIDC provider is probably blocked too; verify with a real create attempt only after the user agrees. Fallback ideas: skip AWS deploy from CI until Phase 6, or use a scoped IAM user key stored in GitHub Secrets (if IAM user creation is allowed).
- S3 skill requires bucket name `<prefix>-<account-id>-<region>-an` with `--bucket-namespace account-regional`.

## Open questions / blockers
- AWS budget is created; whether a hard spend limit is set is still unconfirmed.
- ~~DagsHub token~~ In local `.env` (gitignored) and verified: MLflow endpoint `https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform.mlflow` returns 200. `.env` also has MLFLOW_TRACKING_*, AWS_REGION, AWS_PROFILE.

## Preferences
- No AI attribution in commits/PRs (also saved in the user's Claude memory).
- AWS help level: **HIGH** (set 2026-11-08). User wants the AI to do almost all AWS work; explain each step, suggest alternatives, flag best practices, still follow the user's choice.
- DVC remote: **S3** (decided 2026-11-08). AWS spend limit/budget alerts: user says set.

## Resources (fill in as created; no credentials here)
| Item | Value |
|---|---|
| GitHub repo | https://github.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform |
| CODEOWNERS handles | `@Hashir-Rauf`, `@JA-Codex` (third member _TBD_) |
| DagsHub repo | https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform |
| DVC remote (S3 bucket) | `s3://credit-risk-dvc-<account-id>-ap-southeast-2-an/dvc` (created 2026-11-08: private, versioned, SSE-S3 + Bucket Keys, SSE-C blocked, HTTPS-only policy, ABAC on, tag project=credit-risk, no access logging) |
| MLflow tracking URI | https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform.mlflow (auth via `.env`) |
| AWS region / CLI profile | ap-southeast-2 / `credit-risk` |
| GitHub OIDC provider / deploy role | _TBD_ |
| ECR repo / ECS cluster | _TBD_ |
| API endpoint | _TBD_ (stopped when not demoing) |
| Spend limit / budget alert configured? | Budget created (user confirmed 2026-11-08); hard spend limit unconfirmed |

## Metrics baseline (fill after first runs)
| Model | ROC-AUC | PR-AUC | Notes |
|---|---|---|---|
| Logistic Regression | _TBD_ | _TBD_ | baseline |

## Lessons learned / gotchas
- Tool calls (shell, file writes) occasionally fail with a transient "classifier no verdict" error; retry later or continue other work.
- Dependencies in `requirements.txt` are ranges. After the first clean install, run `pip freeze` into a lock file. Airflow and Kubeflow need separate requirement files (Phase 5).
- Add DVC merge-conflict steps, Kubeflow memory settings, and AWS permission surprises here as they come up.

## Changelog
- 2026-11-08 — Installed AWS CLI and signed in (`credit-risk`, ap-southeast-2); installed the Agent Toolkit (skills + `aws-mcp`); created `CLAUDE.md` with AWS rules; instructor approved the problem; rewrote this file as the handoff.
- 2026-11-08 — Added Phase 0 files (requirements, Makefile, pyproject, .env.example, PR template, CODEOWNERS, README stub); added `phases.md`.
- 2026-11-05 — Read spec; wrote `plan.md`; added Next.js frontend; created `.gitignore`, `agents.md`, `rules.md`, `memory.md`.
