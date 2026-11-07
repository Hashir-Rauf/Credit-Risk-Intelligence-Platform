# S1 (Data & ML) interface contract

Owner: `@JA-Codex`. Consumers: `@Hashir-Rauf` (pipelines, serving, CI, monitoring, frontend).
Status legend: **Live** = implemented and tested; **Planned (Cn)** = will land in that cycle; names may still shift until then, and any change is announced here first.

## 1. Ownership
CODEOWNERS (`.github/CODEOWNERS`) is authoritative where it differs from `plan.md`.
- `@JA-Codex`: `src/data/`, `src/features/`, `src/models/`, `feature_repo/`, `tests/`, `dvc.yaml`, `.dvc/config`.
- `@Hashir-Rauf`: `frontend/`, `pipelines/`, `.github/`, `src/serving/`, `src/monitoring/`, `docker/`, `docker-compose*.yml`, `k8s/`, `infra/`, `monitoring/`.
- Not covered by CODEOWNERS today (suggest adding): `src/common/`, `configs/`, `params.yaml`, `requirements*.txt`, `pyproject.toml`, `docs/`, `data/`.

## 2. Stage interface (Live)
Every S1 stage is a module exposing `run(params: dict, smoke: bool) -> dict` plus a CLI built with `src.common.cli.stage_main`:

```
python -m src.<pkg>.<module> [--params params.yaml] [--smoke] [--log-level INFO]
```
`dvc repro`, Kubeflow components and Airflow tasks call this same entry point; nobody reimplements stage logic.

| Exit code | Meaning | Caller should |
|---|---|---|
| 0 | OK | continue |
| 1 | Error (crash, bad config, bad CLI usage) | retry per policy, then fail |
| 2 | Data validation failed | stop the DAG, do not retry |
| 3 | Quality gate failed | stop, do **not** deploy |

`--smoke` = tiny/fast mode for CI and tests. Never used for real runs. Seeds are applied before `run` from `params.yaml: seed`.

## 3. Parameters (Live)
`params.yaml` is the single source of truth (seed, paths, split, sensitive columns, gate threshold, MLflow/registry names). It is validated on load; invalid files exit 1. Model hyperparameters live in `configs/model_params.yaml`. A relative `--params` path is resolved from the repository root, not the current directory. Only environment-variable **names** are committed (`configs/pipeline_config.yaml`).

## 4. Data and artifacts (Planned)
| Artifact | Path | Cycle |
|---|---|---|
| Raw data (DVC) | `data/raw/default_credit_card.csv` | C1 |
| Validation report | `reports/validation_report.json` | C1 |
| Train/val/test splits (DVC) | `data/processed/{train,val,test}.parquet` | C2 |
| Metrics for gates and CI | `reports/metrics.json` | C2 |
| Model artifact | MLflow model (sklearn pipeline incl. fitted preprocessing) | C2 |
| Feature schema for the API | `reports/feature_schema.json` (names, dtypes, ranges) | C2 |
| Reference data for drift | `data/processed/reference.parquet` | C5 |

Proposed canonical columns (finalised in C1 once the real file is inspected): `ID`, `LIMIT_BAL`, `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`, `PAY_1..PAY_6` (UCI's `PAY_0` renamed to `PAY_1`), `BILL_AMT1..6`, `PAY_AMT1..6`, target `default_next_month`.

The UCI file has no row timestamps. Feast's `event_timestamp` and any "time-ordered" batches are **synthetic** and must be labelled simulated (rules.md §9).

## 5. Model registry (Planned, C2/C3)
- Registered model: `credit-risk-default-model`. Promotion = moving an **alias**; MLflow stages and a `Production` alias are not used.
- `challenger`: newest candidate that passed the gate. `champion`: approved, currently deployed. Serving loads `models:/credit-risk-default-model@champion`.
- Python helpers in `src/models/registry.py`: register, set alias, load by alias, `passes_gate(metrics, params)`.

## 6. Required MLflow run tags and params (Planned, C2)
`git_commit_sha`, `dvc_data_hash`, `feast_repo_commit` (spec §12.1), `algorithm`, `seed`, `smoke` (true/false), plus metrics (ROC-AUC, PR-AUC, KS, recall/precision at threshold) and training duration. Tracking URI comes from `$MLFLOW_TRACKING_URI`.

## 7. Sensitive attributes (Live config, enforced from C2)
`SEX`, `MARRIAGE`, `AGE` are excluded from model inputs (`features.exclude_sensitive_from_model: true`) and used only for fairness reporting. They are not part of Feast training feature views.

## 8. Handoffs to Hashir
| Cycle | You get | Used by |
|---|---|---|
| C0 (now) | Stage CLI + exit codes, params contract, pinned requirements | Airflow tasks, KFP components, CI |
| C1 | `python -m src.data.validation` (exit 2 on bad data), `dvc.yaml` ingest→validate | Airflow validate task, DVC remote setup |
| C2 | Model v0 artifact + `feature_schema.json` | Flask request schema, `/model-info`, Docker |
| C3 | Registry helpers, alias names, gate function | Serving loader, KFP evaluate/register, CI gate |
| C4 | Feast repo + `materialize.py` | Online retrieval in serving |
| C5 | Challenger-vs-champion compare, drift-injection and reference data, explanations | Retrain DAG, Evidently job, frontend |
