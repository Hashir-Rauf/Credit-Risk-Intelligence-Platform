# Cycle 4 testing: Feast feature store

## Scope
`src/features/feast_features.py` (new), `feature_repo/` (new: `feature_store.yaml`, `feature_definitions.py`, README), `train.py`/`tune.py`/`evaluate.py` (read raw features through Feast when `features.use_feast_for_training: true`), `tracking.py` (`feast_repo_commit`, tag `features_from_feast`), `inference.py` (`predict_for_customers`), `config.py`/`paths.py`/`errors.py` (`FeatureNotFoundError`), `params.yaml` (`feast`, `features.use_feast_for_training`), `dvc.yaml` (`feast` stage), `requirements.txt`.

**Dependency change you should know about:** Feast 0.67 requires `pandas<3`, so `pandas` is now pinned to **2.3.3** (was 3.1.1). The whole suite (Cycles 0-4) was re-run on this stack. Use Python 3.11 (Feast installs fine there; keep the venv on 3.11).

What is stored: the 20 raw model inputs in three views (`payment_history`, `billing`, `account`), entity `customer_id`. Engineered features are not stored (they are computed inside the model, so training and serving cannot skew). `SEX`/`MARRIAGE`/`AGE` are never stored. **Timestamps are simulated** (UCI has none): a deterministic function of (seed, customer id) inside `feast.simulated_start` + `simulated_days`; training rows are looked up `label_lag_days` after the snapshot.

## Run
```
pip install -r requirements-dev.txt
ruff check . ; black --check .
pytest -q tests/test_feast.py                # about 40 s
pytest -q                                    # everything (about 7 min)
python -m src.features.feast_features --smoke   # after ingest/validate/preprocess --smoke
dvc repro                                    # ingest -> validate -> preprocess -> feast -> tune -> train -> evaluate
```

## Automated tests
`tests/test_feast.py` (51). Other test modules default to `use_feast_for_training: false` (fast); Feast is exercised here.

| Area | Proves |
|---|---|
| Definitions | Views cover exactly the 20 model inputs, no overlap, no sensitive column; entity, TTL and `simulated` tag present; `feature_store.yaml` agrees with the Python `RepoConfig`; `feature_repo/feature_definitions.py` exposes the same objects |
| Simulated timestamps | Inside the window and UTC; spread over the whole window; a pure function of (seed, id) so any subset gets identical times; seed and window params respected; negative ids rejected |
| Source frames | One frame per view with key, timestamp and int64 features; values equal the raw data; no sensitive columns or `ID`; duplicate ids and missing columns are errors |
| Config | 10 bad `feast.*`/flag values rejected, including `use_feast_for_training` with `exclude_sensitive_from_model: false` |
| Feast stage | 9 quality checks pass and are reported (no sensitive features, views cover inputs, equal rows, unique customers, null fraction, timestamps in window, online store materialised and equal to source, offline equal to source, **no point-in-time leak**); outputs exist; registry lists views and entity; reruns are byte-identical; refuses unvalidated data; a failed check writes the report then exits **2**; smoke stays under `data/smoke/feast` |
| Retrieval | **Offline retrieval equals the preprocessed splits for every customer** (skew guard); id order preserved; online equals offline (int64); unknown customer raises `FeatureNotFoundError` with the ids (never a silent NaN); a lookup one second before the snapshot returns nothing, exactly at it returns the row, beyond the TTL fails loudly |
| Training from Feast | `attach_feast_features` is a no-op when the flag is off; tampered parquet values are overwritten by the store's (proves the store is the source); target, sensitive columns and order untouched; **a full pipeline fed by Feast gives bit-identical val/test metrics to the parquet pipeline**; MLflow tags `features_from_feast=true` and a real `feast_repo_commit`; scoring by customer id equals scoring the raw row |
| Repo version tag | `$FEAST_REPO_COMMIT` override; git commit (+`dirty`); content-hash fallback that changes with the files |
| Wiring | `dvc.yaml` has the `feast` stage; train/tune/evaluate depend on `data/feast/source`; real `dvc repro` runs all 7 stages and a failing gate still stops it |

## Manual checks (on your machine)
| # | Step | Expected |
|---|---|---|
| 1 | Fresh Python 3.11 venv, `pip install -r requirements-dev.txt`, `pytest -q` | all pass (pandas 2.3.3, Feast 0.67) |
| 2 | **Real data:** `dvc repro` | `feast` stage reports `30000 customers, 9 checks passed`; `reports/feast_report.json` has `"status": "passed"` |
| 3 | `python -c "from src.common.config import load_params as l; from src.features.feast_features import *; print(get_online_features(l(), [1, 2, 3]))"` | three rows of the 20 raw features (online store, SQLite) |
| 4 | Same with `[1, 99999999]` | `FeatureNotFoundError` listing `99999999` |
| 5 | `cd feature_repo` then `PYTHONPATH=.. python -m feast apply` (PowerShell: `$env:PYTHONPATH=".."`) | the CLI sees the entity and 3 feature views (optional; the pipeline does not need the CLI) |
| 6 | Compare `reports/metrics.json` of a run with `features.use_feast_for_training: false` and `true` | identical test metrics |

## Recorded results (what was actually run)
- Linux, **Python 3.11.18, pandas 2.3.3**, numpy 2.4.6, scikit-learn 1.9.1, Feast 0.67.0, MLflow 3.18.0, Optuna 5.0.0, DVC 3.67.1.
- `ruff` and `black` clean. `pytest -q`: **391 passed** (about 7.5 min), covering Cycles 0-4 on the pandas-2 stack, including real `dvc repro` through the new `feast` stage (7 stages) and a failing gate.
- Smoke chain `ingest -> validate -> preprocess -> feast` ran end to end with all 9 checks passing.
- Mutation checks, all caught: `attach_feast_features` made a no-op; the point-in-time check inverted (lookups moved after the snapshot); a sensitive column added to a feature view.
- Not run: Windows, real UCI data (so no 30,000-customer build yet), the `feast` CLI itself (`feast apply`; the definitions module is tested by import and by comparing config), a remote/S3 offline store, Redis.

## Known gaps
- Offline store is local parquet and the online store is SQLite (plan.md allows S3/Redis later). On Fargate the SQLite online store must be baked into the serving image or mounted: this is the open "online store packaging" question for Hashir.
- The point-in-time guarantee is shallow by design: one snapshot per customer, so there is no history to join. The checks prove the mechanism (nothing is returned before the snapshot, lookups after it match); they do not prove behaviour on real time series.
- Freshness is checked against the simulated clock (everything in the source was materialised); there is no wall-clock staleness check because the data is a static snapshot.
- Feast prints some deprecation warnings under pandas 2.3.3; they do not affect results.
- Fairness slices (Cycle 5) read `SEX`/`AGE`/`MARRIAGE` from the preprocessed splits, not from Feast.

## Exit checklist
- [x] `ruff` / `black` clean, all automated tests pass on pandas 2.3.3
- [x] Offline retrieval proven identical to the preprocessed data; training from Feast gives identical metrics
- [x] No sensitive attribute in the store (checked in definitions, sources and at runtime)
- [x] Point-in-time and materialisation checks run on every build and fail the stage (exit 2)
- [ ] Manual steps 1-6 on real data
- [ ] Hashir has read the Feast section of `docs/contracts-s1.md` and decided how the online store ships
