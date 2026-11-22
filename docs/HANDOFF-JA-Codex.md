# Handoff for @JA-Codex

Phase 2 items; the status table below is current. Detailed steps:
`phases.md`, Phase 2. Paths you own in `.github/CODEOWNERS`: `src/features/`, `src/models/`,
`feature_repo/`, `notebooks/`, and `tests/` (except the data tests).

## Already done (by @Hashir-Rauf)
- Data ingestion, validation, DVC pipeline (`ingest -> validate`), remotes, `params.yaml`.
- Get the data: `dvc pull -r dagshub` (see README). Clean columns: `customer_id`, `LIMIT_BAL`,
  `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`, `PAY_1..PAY_6`, `BILL_AMT1..6`, `PAY_AMT1..6`, target `default`.
- MLflow tracking URI (DagsHub) is in the maintainer's local `.env`; ask for DagsHub collaborator
  access and your own token. Never commit tokens.

## Status (2026-11-20)
| Item | File | Status |
|---|---|---|
| Preprocessing, features, split, feature schema | `src/features/preprocessing.py` | done, tested |
| Train: logistic regression, random forest, HistGradientBoosting; `tuned` mode | `src/models/train.py` | done, tested |
| Optuna tuning, 20 trials x 3 families, nested MLflow runs, comparison table | `src/models/tune.py` | done, tested (val split only) |
| Evaluation, plots, gate (exit 3), regression check | `src/models/evaluate.py` | done, tested |
| Registry (`challenger`), promotion (`champion`), fairness rule | `src/models/{registry,promote}.py` | done, tested |
| Feast repo, 9 quality checks, simulated timestamps | `feature_repo/`, `src/features/feast_features.py` | done, tested |
| Fairness report + ablation, SHAP/linear explanations | `src/models/{fairness,explain}.py` | done, tested |
| Simulated drift scenarios + reference data | `src/models/drift.py` | done, tested |
| `dvc.yaml`: 10 stages | `dvc.yaml` | added (Hashir to review) |
| Real-data run of tune/train/evaluate/fairness/explain/drift | `reports/` | run once (numbers in the docs); **re-run after the CV change, then `dvc push`** |
| Train-CV stability check in tuning (`tune.cv_folds: 3`) | `src/models/tune.py` | done, tested; real numbers pending the re-run |
| EDA notebook | `notebooks/eda.ipynb` | written, dry-run verified on synthetic data; **run on real data and fill section 8** |
| `docs/model-selection.md` | `docs/` | written from real numbers; add the CV column after the re-run |
| `docs/fairness.md` | `docs/` | written from real numbers |
| Model card | `docs/model-card.md` | drafted; fill the `<fill>` fields after registry/promote |
| Serving quick reference | `docs/contracts-s1.md` section 8d | done |
| Pinned ML libs + `requirements.lock.txt` (Linux, py3.11) | repo root | done |
| Gate recalibration | `params.yaml: gate` | done: 0.77 |

Deviations from `phases.md`: third family is HistGradientBoosting (not XGBoost/LightGBM, which were removed from `requirements.txt`); the tuning objective is validation AUC, with a 3-fold CV AUC on train reported for each family's best hyperparameters as a stability check (selection does not use it); aliases are `challenger`/`champion` (not `staging`).

## Data notes
30,000 rows, default rate 22.1%, no nulls, no duplicate IDs. `EDUCATION` has undocumented codes
0/5/6 and `MARRIAGE` 0; `PAY_*` uses -2/-1/0 for "no delay / paid" and 1..8 for months late.
35 rows duplicate another row once the ID is ignored (kept).
