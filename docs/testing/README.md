# Testing docs (one per development cycle)

Each cycle ships `docs/testing/cycle-N.md` with: scope, how to run, what each test proves, manual checks, recorded results, known gaps and an exit checklist. A cycle is done only when its exit checklist is fully ticked.

| Cycle | Doc | Status |
|---|---|---|
| 0 | [cycle-0.md](cycle-0.md) | Automated tests pass (Linux, Python 3.11) |
| 1 | [cycle-1.md](cycle-1.md) | Automated tests pass; real-UCI manual check pending |
| 2 | cycle-2.md | Not started |

## Standard commands
```
pip install -r requirements-dev.txt
ruff check . && black --check .
pytest -q                                   # all tests
pytest -q -m "not integration"              # skip real git/dvc subprocess tests
pytest -q --cov=src --cov-report=term-missing
pytest -q -k <keyword>                      # one area
```
PowerShell activation: `python -m venv .venv; .\.venv\Scripts\Activate.ps1`. Bash: `source .venv/bin/activate`.

## Conventions
- Tests live in `tests/`; spec categories map to files: `test_data.py`, `test_features.py`, `test_feature_store.py`, `test_model.py`, `test_regression.py` (S1), `test_api.py`, `test_pipeline.py` (S3/S2).
- Tests must be deterministic (seeded) and fast; anything slow is gated behind `--smoke`-sized data.
- Never weaken or delete a failing test to get green (rules.md §5). If a result in a cycle doc was not actually run, it says so.
