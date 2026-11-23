# Phase 2 close-out runbook (JA-Codex, on your machine)

Do these in order. They need your DagsHub access, the real data and your machine, so they cannot be done in the cloud session. Tick each box; each step says what "done" looks like.

Setup (once): Python **3.11** venv (`py -3.11 -m venv .venv; .venv\Scripts\Activate.ps1`), then `pip install -r requirements-dev.txt`. Use `requirements.txt` on Windows (the lock file is Linux-only, for Docker and CI). Copy `.env.example` to `.env` and fill the DagsHub MLflow URI and token (never commit it).

## 1. Check the code (5-15 min)
```
ruff check . ; black --check .
pytest -q
```
- [ ] Done when ruff and black are clean and pytest passes (about 12 min). If `test_every_artifact_exists` fails, paste the traceback to me; do not skip it.

## 2. Pull the data (1 min)
```
dvc pull -r dagshub
```
- [ ] `data/raw/credit_default.csv` exists (30,000 rows).

## 3. Run the whole pipeline on real data (10-30 min, mostly tuning)
```
dvc repro
```
- [ ] All 10 stages finish. `evaluate` exiting with code 3 means the gate failed: stop and send me `reports/evaluation.json`.
- [ ] `reports/tuning.json` now has `cv_roc_auc_mean/std` per family. Open `reports/model_comparison.md`: check the CV mean is close to the validation AUC (within about 0.01). A big gap means the validation number is lucky.
- [ ] The winner may differ from the previous run (random forest vs HistGradientBoosting were tied; Optuna and library versions changed). If it changed, `docs/model-selection.md`, `docs/model-card.md` and `docs/fairness.md` must be rewritten from the new `tuning.json`, `metrics.json`, `fairness.json`. Send me those three files and I will do it.
- [ ] `reports/drift_scenarios.json` has `"all_match_expectation": true`.
- [ ] `reports/explanations.png`: top factors are payment delay and utilisation features. If SEX or AGE-like names appear, stop and tell me.

## 4. Reproducibility check (same seed, same result)
```
copy reports\metrics.json $env:TEMP\metrics_a.json
dvc repro --force train evaluate
fc reports\metrics.json $env:TEMP\metrics_a.json
```
- [ ] No differences. (Exit criterion: same seed gives the same metrics.)

## 5. Feast: online equals offline
```
dvc repro feast   # already ran in step 3; check the report
```
- [ ] `reports/feast_report.json` shows the parity check passed (200 sampled customers equal across source, offline and online).
- [ ] For the evidence, run in Python from the repo root:
```
from src.common.config import load_params
from src.features.feast_features import get_online_features
print(get_online_features(load_params(), [1, 2, 3]))
```
Save the output and the `feast apply` log as evidence.

## 6. Register and promote
```
python -m src.models.registry     # registers version, alias "challenger" (exit 3 = gate failed)
python -m src.models.promote      # first challenger becomes "champion"
```
- [ ] DagsHub MLflow UI shows the model `credit-risk-default-model` with alias `champion`.
- [ ] Copy version, git SHA, DVC data hash and run id from the model page into the `<fill>` fields of `docs/model-card.md`.

## 7. Evidence for the report (`docs/evidence/phase-2/`)
Create the folder and put in:
- [ ] `mlflow_comparison.png`: DagsHub MLflow experiments page, tuning run with its 3 nested family runs, comparison view.
- [ ] `registry.png`: registered model page with the `champion` alias.
- [ ] `feast_online_vs_offline.txt`: output from step 5.
- [ ] `fairness_table.md`: copy the table from `docs/fairness.md` (regenerate if step 3 changed it).
- [ ] `dvc_repro.log` (`dvc repro 2>&1 | Tee-Object dvc_repro.log` on a forced re-run) and `dvc dag` output.
- [ ] `README.md` in that folder: one line per file saying what it proves (copy the style of `docs/evidence/phase-1/README.md`).

## 8. EDA notebook
```
jupyter lab notebooks/eda.ipynb
```
- [ ] Run all cells on the real data, fill section 8 ("Findings") in your own words, and save with outputs.

## 9. Fairness decision (your call, then write it down)
`AGE_BAND` fails two checks (TPR gap 0.128 vs 0.10, AUC gap 0.083 vs 0.05) and `fairness.enforce` is `false`.
- Option A (current): keep reporting only, state in `docs/fairness.md` that the gap is accepted and monitored in Phase 8. Fine for a course project if explained.
- Option B: set `enforce: true` only if you also fix the gap (group-aware thresholds, reweighting). Otherwise `evaluate`/promotion will block your own model.
- [ ] Whatever you choose, the "Decision" paragraph in `docs/fairness.md` must say it plainly. Re-check the numbers if step 3 changed the model.

## 10. Hand over
- [ ] Commit `dvc.lock`, `reports/*.json` that are git-tracked, notebook, docs, and run `dvc push -r dagshub` and `dvc push -r s3` (Hashir's CI needs the data and model artifacts).
- [ ] No AI attribution lines in commits (rules.md section 11). Branch `feat/phase-2-modelling`, open a PR, ask Hashir to review `dvc.yaml`/`params.yaml` (he owns them).
- [ ] Tell Hashir: use `docs/contracts-s1.md` section 8d for serving; Python 3.11; install from `requirements.lock.txt` in Docker; validation exit code 1 vs 2 and online-store packaging are his open items.
