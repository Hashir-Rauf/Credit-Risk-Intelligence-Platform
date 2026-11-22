# Cycle 3 testing: tuning, model selection, registry, promotion, regression

## Scope
`src/models/{tune,registry,promote}.py` (new), `train.py` (HistGradientBoosting, `tuned` mode, run tags), `evaluate.py` (`check_regression`), `tracking.py` (`registry_model_name`), `config.py`/`paths.py`, `params.yaml` (`tune`, `promotion`, `train.algorithm: tuned`), `configs/model_params.yaml` (search spaces), `dvc.yaml` (`tune` stage), `requirements.txt` (optuna 5.0.0), `tests/baselines/synthetic_lr.json`.

**Deviation from plan.md:** the third model family is scikit-learn's `HistGradientBoostingClassifier` instead of XGBoost/LightGBM. Same model class (gradient-boosted trees), no extra native dependency in the Docker image, no extra skops trust surface. Swapping in XGBoost later only means a new family in `build_estimator` and `search_spaces`.

## Run
```
pip install -r requirements-dev.txt
ruff check . ; black --check .
pytest -q                                    # everything (about 5 min, mostly real MLflow + DVC)
pytest -q tests/test_tuning.py tests/test_regression.py
dvc repro                                    # ingest -> validate -> preprocess -> tune -> train -> evaluate
python -m src.models.registry                # register the evaluated model, alias "challenger" (exit 3 if gate failed)
python -m src.models.promote                 # challenger -> champion if it wins (exit 3 if not)
# smoke (synthetic, fewer trials), same chain with --smoke on every command
PYTHONPATH=. python tests/test_regression.py --update   # only after an intended model change
```

## Automated tests
`tests/test_tuning.py` (64), `tests/test_regression.py` (5); `tests/conftest.py` defaults tests to the fast logistic baseline and tiny trial counts.

| Area | Proves |
|---|---|
| Search spaces | Each spec type maps to the right Optuna call; log ints ignore `step`; unknown type is a clear error; fixed defaults survive, searched keys override |
| Selection | Best validation ROC-AUC wins; a simpler family wins within `simplicity_margin` (boundary inclusive, float-safe); exact ties prefer the simpler model |
| Tune stage | Report holds all families with N trials each and `best == max(trials)`; best params merge fixed defaults; comparison markdown names every family and the reason; byte-identical on rerun; a different seed changes the search; **replacing the test labels cannot change the result** (test split never read); smoke uses fewer trials and stays in `data/smoke`; MLflow has one parent run and one nested run per family; missing search space and missing splits fail clearly |
| Train with `tuned` | Trains the selected algorithm with its exact hyperparameters and reproduces the search's validation AUC; run tagged `run_type=training`, `tuned=True`, with `tuning.json` and the comparison attached; fails clearly without the tune stage; random forest and gradient boosting both train, log and reload through MLflow; sensitive columns never reach the boosted model |
| Regression check | `check_regression` flags each metric that drops more than the allowance (boundary allowed); stored synthetic baseline holds; **a model trained on shuffled labels is flagged**, so the check is not vacuous |
| Promotion rules | First champion; clear win; gain below minimum rejected; gain exactly at minimum passes; a drop in PR-AUC/KS blocks despite an AUC gain; fairness report required only when configured; worse candidate rejected |
| Registry | Registers with required version tags and sets `challenger`; idempotent per training run; new run = new version; loaded model scores identically to the saved one; failed gate refused (exit 3) and stale `evaluation.json` refused; unknown alias handled; smoke uses a separate model name |
| Promote | First challenger becomes champion and the challenger alias is cleared; no challenger / already champion are errors; an equal challenger is rejected (exit 3), champion untouched, decision recorded and tagged `rejected`; a better one replaces the champion; fairness requirement blocks when the report is missing |
| Config / wiring | 10 bad `tune`/`promotion`/`train` values rejected; real defaults meet the spec (>= 20 trials, 3 families, `tuned`); every family has defaults and a search space; `dvc.yaml` has the `tune` stage and consistent deps; real `dvc repro` runs all 6 stages and a failing gate still stops it |

## Manual checks (on your machine)
| # | Step | Expected |
|---|---|---|
| 1 | `pytest -q` in a fresh venv | all pass |
| 2 | **Real data:** `dvc repro` | tune runs 3 families x 20 trials (a few minutes; random forest is the slow one), then train and evaluate; `reports/model_comparison.md` shows the table and the selected model |
| 3 | `mlflow ui --backend-store-uri sqlite:///mlflow.db` | one `tuning` run with 3 nested family runs, one `training` run |
| 4 | `python -m src.models.registry` then `python -m src.models.promote` | version 1 registered, becomes `champion`; `reports/registration.json` and `reports/promotion.json` written |
| 5 | Run the whole chain again with a changed hyperparameter | new version registered; promote either replaces the champion (gain >= 0.005) or exits 3 with the reason in `promotion.json` |
| 6 | **Send me `reports/tuning.json`/`model_comparison.md` and `metrics.json`** | I calibrate `gate.roc_auc_min` and write the model-selection justification from real numbers |

## Recorded results (what was actually run)
- Linux, Python 3.11, scikit-learn 1.9.1, MLflow 3.18.0, Optuna 5.0.0, DVC 3.67.1.
- `ruff` and `black` clean. `pytest -q`: **340 passed** (about 5 min), including real `dvc repro` through the new `tune` stage and a failing gate.
- Smoke chain (`--smoke`: ingest ... tune, train, evaluate, registry, promote) ran end to end, including the first-champion promotion.
- Mutation checks: gate bypass in registry, tuning on the test split instead of validation, and the earlier gate/ratio mutants were caught; `<` vs `<=` on the promotion minimum is an equivalent mutant (epsilon-tolerant comparison).
- Bugs found by tests: HistGradientBoosting could not be logged (skops needed `TreePredictor` trusted; fixed for that family only); selection margin was wrong at the exact boundary because of float error (epsilon added).
- Not run: Windows, real UCI data (so no real tuning numbers yet), a remote MLflow server.

## Known gaps
- The written justification of the selected model needs real-data numbers (manual step 6); the stage only generates the evidence table and the selection reason.
- Promotion re-scores both models on the same test split, so after many rounds the test split slowly becomes a selection set. Fine for this project; for production a fresh holdout is needed.
- Fairness is not part of promotion yet (`require_fairness_report: false`); Cycle 5 produces `reports/fairness.json` and flips it on.
- `register`/`promote` change MLflow state, not files, so they are not DVC stages. Airflow/KFP call them as commands after `dvc repro` (exit codes in `docs/contracts-s1.md`).
- No remote MLflow server or DagsHub tested; `$MLFLOW_TRACKING_URI` is honoured everywhere, aliases need a server with a database backend (SQLite works locally).

## Exit checklist
- [x] `ruff` / `black` clean, all automated tests pass
- [x] Test split provably unused by tuning
- [x] Selection, promotion and regression rules covered with boundary tests
- [x] Registry round trip (register, alias, load) verified against a real MLflow store
- [ ] Manual steps 2-6 on real data
- [ ] Hashir has read the new contract sections (registry, promote, exit codes)
