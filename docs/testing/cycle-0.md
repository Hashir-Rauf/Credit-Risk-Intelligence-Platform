# Cycle 0 testing: skeleton, config, seeding, stage CLI

## Scope
`src/common/{config,seed,logging_utils,cli,errors}.py`, `params.yaml`, `configs/*.yaml`, pinned requirements, package skeleton (`src/{common,data,features,models}`). No data or models yet.

## Run
```
pip install -r requirements-dev.txt
ruff check . && black --check .
pytest -q --cov=src --cov-report=term-missing
```
Target runtime: Python 3.11.

## Automated tests (`tests/test_common.py`, 57 cases)
| Class | Proves |
|---|---|
| `TestConfig` | Real `params.yaml` validates; every required key present; all missing keys reported at once; each invalid value is rejected (bad seed types, unknown `data.source`, split not summing to 1, empty/invalid sensitive columns, sensitive overlapping id/target, gate outside (0.5, 1), identical aliases); missing/broken/non-mapping YAML raises `ConfigError`; sensitive attributes excluded by default; aliases are `challenger`/`champion`; `exit_codes` in `pipeline_config.yaml` match the `ExitCode` enum |
| `TestSeed` | `derive_seed` is stable, in range, differs by name/base, pinned to a known value (changing it would silently change every split); Python and NumPy RNGs reproducible; `PYTHONHASHSEED` exported; `make_rng` reproducible and independent per name |
| `TestLogging` | `configure_logging` idempotent (one handler); level from argument; invalid level falls back to INFO |
| `TestStageMain` | Params loaded and seeded before `run`; `--smoke` flag passed (default false); `DataValidationError`→2, `GateFailedError`→3, `ConfigError`/other exceptions→1; missing or invalid params → 1 and stage not run; bad CLI usage → 1 (argparse's native 2 would be misread as validation failure); `--help` → 0; **real subprocess** returns the exit code Airflow/KFP will see |
| `TestRepoHygiene` | All packages import; every requirement pinned with `==`; `data/raw` and `data/processed` gitignored; param paths relative; no secret-looking keys in `params.yaml` |

## Manual checks
| # | Step | Expected |
|---|---|---|
| 1 | `python -c "from src.common.config import load_params; print(load_params()['seed'])"` | prints `42` |
| 2 | Edit `params.yaml`: set `split.train: 0.9`, run step 1 | `ConfigError` mentioning "sum to 1.0"; revert the edit |
| 3 | `python -c "from src.common.cli import stage_main; raise SystemExit(stage_main(lambda p,s: None, description='x', argv=['--smoke']))"` then `echo $?` (PowerShell: `$LASTEXITCODE`) | `0` |
| 4 | `git add --renormalize . && git status` after pulling `.gitattributes` | line-ending-only diffs disappear; no content changes |

## Recorded results (what was actually run)
- Environment: Linux, Python 3.11.18, numpy 2.4.6, pandas 3.1.1, PyYAML 6.0.3, pytest 9.2.0.
- `ruff check .`: clean. `black --check .`: clean.
- `pytest -q`: **57 passed**. Coverage of `src/`: **100%** (170 statements).
- Not run: Windows/PowerShell, Docker (`Dockerfile.ml` does not exist yet), manual checks 1-4 (steps are for you; automated tests cover the same logic).

## Known gaps
- Cycle 0 has no data or model logic, so no model/data tests yet.
- `pandas 3.x` pinned because it is the current release; if Feast (C4) or MLflow (C2) require older pandas, pins are reconciled then and this doc is updated.
- CI wiring for these tests belongs to Hashir (`.github/`); until then run them locally.

## Exit checklist
- [x] `ruff` and `black` clean
- [x] All automated tests pass
- [x] Exit-code contract verified through a real process
- [ ] Manual checks 1-4 run on your machine (Windows/PowerShell)
- [ ] Hashir has reviewed `docs/contracts-s1.md`
