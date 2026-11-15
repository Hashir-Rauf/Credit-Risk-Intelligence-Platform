# Cycle 2 testing: features, split, baseline model, MLflow, gate

> Ported onto Hashir's Phase 1 base. In the merged tree the old `ingest`/`validate` smoke steps are `python -m src.data.synthetic --smoke`; test counts differ (his 23 data tests plus these).

## Scope
`src/features/preprocessing.py`, `src/models/{evaluate,train,tracking,inference}.py`, `src/common/{io,config}.py` and `src/data/paths.py` (new helpers/keys), `params.yaml` (`features.schema_path`, `train`, `evaluate`, `mlflow.default_tracking_uri`), `dvc.yaml` (`preprocess -> train -> evaluate`), `requirements.txt` (scikit-learn, joblib, pyarrow, matplotlib, mlflow).

## Run
```
pip install -r requirements-dev.txt
ruff check . ; black --check .
pytest -q                                   # everything (about 2 min)
pytest -q -m "not integration"              # skip real dvc/git tests
python -m src.data.ingestion --smoke ; python -m src.data.validation --smoke
python -m src.features.preprocessing --smoke ; python -m src.models.train --smoke ; python -m src.models.evaluate --smoke
```
Real data: `dvc repro` (stops with exit 3 if the gate fails). Browse runs: `mlflow ui --backend-store-uri sqlite:///mlflow.db`.

## Automated tests
`tests/test_features.py` (45) and `tests/test_model.py` (66); `tests/conftest.py` gives every test an isolated tmp dir and MLflow store.

| Area | Proves |
|---|---|
| Feature engineering | Hand-computed example; education grouping; stateless and row-wise (batch == single row, independent of other rows and of the target); input not mutated; zero limit / zero bills give no inf/NaN; ratios clipped; missing column is a clear error |
| Split | Sizes follow params, disjoint IDs covering all rows, stratified, reproducible, seed-dependent |
| Pipeline / leakage | Scaler fitted on train only; tampering with val labels/values cannot change the fitted model; sensitive columns never reach the model (flag on) and only enter when the flag is off; unseen category does not crash; accepts raw API fields |
| Feature schema | Inputs, ranges and allowed values come from `validation` params; excluded and server-side columns documented |
| Preprocess stage | Writes 3 splits + schema; refuses missing/failed validation; byte-identical reruns; smoke isolation; CLI exit codes |
| Metrics / gate | Known AUC/Brier/confusion values; inclusive threshold; invalid input rejected; gate boundary is `>=`; 4 valid PNG plots |
| Tracking | Env var wins; default under repo root; smoke under `data/smoke`; `dvc.lock` hash lookup and graceful fallbacks; required tags present |
| Train / MLflow | Deterministic `metrics.json`; test metrics unchanged when val labels change; random forest and `exclude_sensitive: false` work; MLflow run has tags, params, metrics, plots/reports/config artifacts and a signature; logged model gives the same probabilities as the saved one |
| Inference | Matches the pipeline exactly; **flipping SEX/MARRIAGE/AGE in the payload does not change the score**; bad/missing/null/non-finite fields rejected |
| Evaluate / gate | Failure raises, exits **3**, still writes `evaluation.json` and tags the MLflow run `gate_passed=false`; detects a changed model or test data (does not reproduce training metrics) |
| Config / wiring | Bad training config rejected; `dvc.yaml` order, deps, params, outputs match `params.yaml` |
| `TestDvcRepro` (integration) | Real `dvc repro` runs all 5 stages, second run is a no-op, lowering the bar the model can't meet (`roc_auc_min: 0.999`) makes `dvc repro` fail with the report showing `passed: false` |

## Manual checks (on your machine)
| # | Step | Expected |
|---|---|---|
| 1 | `pytest -q` in a fresh venv | all pass |
| 2 | **Real data:** `dvc repro` | all stages run; `reports/metrics.json` has test ROC-AUC (UCI baselines are typically 0.76-0.78 for logistic regression) |
| 3 | `git add dvc.lock reports/*.json` and commit | clean tree afterwards |
| 4 | `mlflow ui --backend-store-uri sqlite:///mlflow.db` | one run with tags (`git_commit_sha`, `dvc_data_hash` from `dvc.lock`), plots, model |
| 5 | **Send me `reports/metrics.json`** | I calibrate `gate.roc_auc_min` (0.75 is a guess) from the real baseline |
| 6 | Set `gate.roc_auc_min: 0.99`, `dvc repro`, then revert | exit 3; revert and rerun passes |

## Recorded results (what was actually run)
- Linux, Python 3.11, scikit-learn 1.9.1, MLflow 3.18.0, DVC 3.67.1.
- `ruff` and `black` clean. `pytest -q`: **270 passed** (including real `dvc repro` for the new stages). Coverage was not re-measured this cycle.
- Mutation check: gate `>=` -> `>` and ratio clip 1.5 -> 9.0 were both caught by the suite.
- Bug found by tests: MLflow artifacts leaked into `./mlruns` (now `mlartifacts/` beside the DB); random forest models could not be logged to MLflow (skops refuses `sklearn.tree._tree.Tree`); fixed by trusting that type for random forest only.
- Synthetic data clears the 0.75 gate; **real-data metrics are not known yet**.
- Not run: Windows, real UCI data, a remote MLflow server/DagsHub.

## Known gaps
- Gate value 0.75 is unvalidated until manual step 5.
- `feast_repo_commit` tag is a placeholder until Cycle 4.
- MLflow model registry (aliases) and tuning are Cycle 3.
- Serving image must contain `src/features/preprocessing.py` (the saved model references `engineer_features`) or load the MLflow model, which bundles `src/`.
- `joblib` model files unpickle code: load only models our pipeline produced.

## Exit checklist
- [x] `ruff` / `black` clean, all automated tests pass
- [x] Leakage and sensitive-attribute tests pass
- [x] Gate failure exits 3 and stops `dvc repro`
- [x] MLflow run carries all required tags
- [ ] Manual steps 2-6 on real data; baseline metrics sent back
- [ ] Hashir has read section 6-9 of `docs/contracts-s1.md`
