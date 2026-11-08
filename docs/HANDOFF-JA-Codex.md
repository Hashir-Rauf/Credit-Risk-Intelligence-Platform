# Handoff for @JA-Codex

Everything below is **yours to build**. Placeholders exist so you know the file names and function
signatures; each raises `NotImplementedError` and carries the spec in its docstring. Detailed steps:
`phases.md`, Phase 2. Paths you own in `.github/CODEOWNERS`: `src/features/`, `src/models/`,
`feature_repo/`, `notebooks/`, and `tests/` (except the data tests).

## Already done (by @Hashir-Rauf)
- Data ingestion, validation, DVC pipeline (`ingest -> validate`), remotes, `params.yaml`.
- Get the data: `dvc pull -r dagshub` (see README). Clean columns: `customer_id`, `LIMIT_BAL`,
  `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`, `PAY_1..PAY_6`, `BILL_AMT1..6`, `PAY_AMT1..6`, target `default`.
- MLflow tracking URI (DagsHub) is in the maintainer's local `.env`; ask for DagsHub collaborator
  access and your own token. Never commit tokens.

## To do (placeholders marked)
| Item | File | Status |
|---|---|---|
| Preprocessing, engineered features, split | `src/features/preprocessing.py` | placeholder |
| Train 3+ model families | `src/models/train.py` | placeholder |
| Optuna tuning (>= 20 trials/family) | `src/models/tune.py` | placeholder |
| Evaluation + quality gate | `src/models/evaluate.py` | placeholder |
| MLflow logging helper (single shared module) | `src/models/tracking.py` | not created |
| Feast repo (entity `customer_id`, parquet offline, SQLite online) | `feature_repo/` | not created |
| EDA notebook (class balance, odd categories, `PAY_*` coding) | `notebooks/` | not created |
| Model selection doc | `docs/model-selection.md` | not created |
| Fairness analysis (by SEX and age band) + SHAP | `docs/fairness.md` | not created |
| Unit tests for features/models | `tests/` | not created |
| `dvc.yaml` stages `preprocess`, `train`, `evaluate` | `dvc.yaml` | not added (ask @Hashir-Rauf; he owns the file) |
| Quality-gate thresholds | `configs/` | not created |

## Data notes
30,000 rows, default rate 22.1%, no nulls, no duplicate IDs. `EDUCATION` has undocumented codes
0/5/6 and `MARRIAGE` 0; `PAY_*` uses -2/-1/0 for "no delay / paid" and 1..8 for months late.
35 rows duplicate another row once the ID is ignored (kept).
