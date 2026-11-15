# Handoff for @JA-Codex

Phase 2 items; the status table below is current. `tune.py` is still a placeholder. Detailed steps:
`phases.md`, Phase 2. Paths you own in `.github/CODEOWNERS`: `src/features/`, `src/models/`,
`feature_repo/`, `notebooks/`, and `tests/` (except the data tests).

## Already done (by @Hashir-Rauf)
- Data ingestion, validation, DVC pipeline (`ingest -> validate`), remotes, `params.yaml`.
- Get the data: `dvc pull -r dagshub` (see README). Clean columns: `customer_id`, `LIMIT_BAL`,
  `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`, `PAY_1..PAY_6`, `BILL_AMT1..6`, `PAY_AMT1..6`, target `default`.
- MLflow tracking URI (DagsHub) is in the maintainer's local `.env`; ask for DagsHub collaborator
  access and your own token. Never commit tokens.

## Status (2026-11-15)
| Item | File | Status |
|---|---|---|
| Preprocessing, engineered features, stratified split, feature schema | `src/features/preprocessing.py` | done, tested |
| Baseline train (logistic regression, random forest) + MLflow logging | `src/models/train.py`, `tracking.py` | done, tested |
| Evaluation, plots, quality gate (exit 3) | `src/models/evaluate.py` | done, tested |
| Scoring helpers for the API | `src/models/inference.py`, `reports/feature_schema.json` | done, tested |
| `dvc.yaml` stages `preprocess`, `train`, `evaluate` | `dvc.yaml` | added (Hashir to review) |
| Real-data baseline run, gate calibration | `params.yaml: gate` | **not run yet** |
| Boosted-tree family (xgboost / lightgbm) | `src/models/train.py` | not started |
| Optuna tuning (>= 20 trials/family) | `src/models/tune.py` | placeholder |
| Registry helpers (aliases challenger/champion) | `src/models/registry.py` | not created |
| Feast repo (entity `customer_id`, parquet offline, SQLite online) | `feature_repo/` | not created |
| EDA notebook | `notebooks/` | not created |
| Model selection doc | `docs/model-selection.md` | not created |
| Fairness analysis (SEX, age band) + SHAP | `docs/fairness.md` | not created |

## Data notes
30,000 rows, default rate 22.1%, no nulls, no duplicate IDs. `EDUCATION` has undocumented codes
0/5/6 and `MARRIAGE` 0; `PAY_*` uses -2/-1/0 for "no delay / paid" and 1..8 for months late.
35 rows duplicate another row once the ID is ignored (kept).
