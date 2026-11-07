# Cycle 1 testing: data ingestion, validation, DVC pipeline

## Scope
`src/data/{ingestion,validation,schema,synthetic,paths}.py`, `src/common/io.py`, new `params.yaml` sections (`data`, `smoke`, `validation`), `dvc.yaml` (`ingest -> validate`), `.dvc/`, `.dvcignore`, `docs/dvc-runbook.md`. Also touched: `src/common/{config,cli,errors}.py` (new keys, `IngestionError`, exit-code mapping).

## Run
```
pip install -r requirements-dev.txt
ruff check . ; black --check .
pytest -q                                   # everything (about 10 s)
pytest -q -m "not integration"              # skip the real dvc/git tests (about 3 s)
pytest -q --cov=src --cov-report=term-missing
python -m src.data.ingestion --smoke ; python -m src.data.validation --smoke   # CI-style smoke run, synthetic data
```

## Automated tests
`tests/test_data.py` (101 cases) and additions to `tests/test_common.py` (now 58).

| Class | Proves |
|---|---|
| `TestSynthetic` | Canonical columns, all int64; deterministic per seed; sequential unique IDs; default rate 18-27%; `PAY_1` carries signal; real-data quirks present (EDUCATION 5/6, MARRIAGE 0, PAY -2/0); passes validation |
| `TestPaths` | Normal paths follow `params.yaml`; smoke paths are isolated under `data/smoke/` |
| `TestNormalizeColumns` | UCI, Kaggle and messy headers map to canonical names (`PAY_0`->`PAY_1`, target renamed); `PAY_0`+`PAY_1` together is an error; unknown columns survive so validation can report them; integral floats become int64, fractions and nulls stay visible |
| `TestReaders` | CSV, empty CSV, unsupported type; **real `.xls` built with xlwt in the UCI layout (label row above header)** parses to the canonical frame; headerless and corrupt `.xls`; zip selection, no table in zip, not a zip |
| `TestDownload` | User-Agent and timeout; network failure, oversize and plain-http refused (no real network used) |
| `TestS3` | Bucket/key parsing; bad or unset `RAW_DATA_S3_URI`; S3 failure; missing boto3; full run from an S3 zip of an `.xls` (fake client, no AWS) |
| `TestIngestionRun` | Synthetic run writes CSV + report with sha256 and `synthetic: true`; byte-identical across runs and LF-only; seed changes data; local csv/xls; missing local file; mocked UCI download end to end (ingest -> validate passes, data equals source); **`--smoke` ignores `source: s3`, writes only under `data/smoke/`, never `data/raw`**; extra columns kept and missing ones reported by validation; CLI exit codes |
| `TestValidation` | 20 parametrised violations each fail with the right column and check: null, duplicate ID, SEX/EDUCATION/MARRIAGE out of domain, AGE/PAY/PAY_AMT/BILL_AMT/LIMIT_BAL out of range, bad target, string in numeric column, float in int column, extra column, missing column, target rate 0% and 100%, too few rows; clean data passes; undocumented codes and duplicate rows only warn; all problems reported together; report is JSON, examples capped at 3, frame not mutated; params changes take effect |
| `TestValidationRun` | Deterministic report bytes; failure writes the report **then** raises; CLI exit 0 / **2** (bad data) / 1 (missing raw file); unreadable CSV fails with a report; smoke isolation |
| `TestNewConfigValidation` | Bad URL scheme, non-positive counts, bad target-rate bounds, empty domains, inverted ranges, bad undocumented codes, unknown source |
| `TestDvcWiring` | `dvc.yaml` stages and commands; validate depends on ingest output; output paths equal `params.yaml` paths; reports are `cache: false`; declared params exist; all deps exist; `.dvcignore` excludes bytecode; `.dvc/config` holds no credentials; `.gitignore` covers smoke data and `config.local` |
| `TestDvcReproducibility` (integration) | In a throwaway git repo: `dvc repro` works; second run skips both stages; changing the seed changes the data hash; checking out the old commit + `dvc checkout` restores the old hash. A failing validation stops `dvc repro` (exit 2) |

## Manual checks (on your machine)
| # | Step | Expected |
|---|---|---|
| 1 | `pytest -q` in a fresh venv (Windows/PowerShell) | all pass; DVC integration tests included |
| 2 | **Real data:** `python -m src.data.ingestion` then `python -m src.data.validation` | ingest writes `data/raw/default_credit_card.csv` (30,000 rows x 25 cols); validation exit 0 with `undocumented_codes` warnings; `reports/validation_report.json` has `"status": "passed"`, target rate near 0.22 |
| 3 | `echo $LASTEXITCODE` after step 2 | `0` |
| 4 | Edit one value in `data/raw/default_credit_card.csv` (e.g. `SEX` to 7), rerun validation | exit code `2`; report names `SEX` with `isin([1, 2])`. Restore with `dvc checkout --force` (plain `dvc checkout` refuses to overwrite edited files) and `git checkout -- reports` |
| 5 | `dvc repro`, commit `dvc.lock`, then run the reproducibility demo in `docs/dvc-runbook.md` section 4 | restored sha256 matches the old commit's `ingestion_report.json` |
| 6 | Set `validation.min_rows: 99999`, `dvc repro` | fails with `exited with 2`; revert the change |

Step 2 is the important one: I could not reach the UCI site from my environment, so the real download and real `.xls` layout are **unverified**. Everything else above ran against realistic fixtures.

## Recorded results (what was actually run)
- Environment: Linux, Python 3.11.18, pandas 3.1.1, pandera 0.35.0, python-calamine 0.9.1, DVC 3.67.1, pytest 9.2.0.
- `ruff check .` clean; `black --check .` clean.
- `pytest -q`: **159 passed** (157 without the 2 integration tests). Coverage of `src/`: **99%** (533 statements; the 7 uncovered lines are `__main__` guards and defensive branches).
- Mutation check (each defect injected, suite must fail): dropping the SEX domain, skipping the `PAY_0` rename, making smoke write to `data/raw`, disabling the target-rate check and writing CRLF CSVs were all **caught**. Disabling JSON key sorting was not caught; it does not affect determinism, only key order.
- DVC rehearsal in a throwaway repo (local remote, synthetic data): repro, no-op rerun, data change, checkout of the old version restores the old hash, `dvc pull` after cache deletion, `dvc.lock`/`params.yaml` merge conflict resolved, `.dvc` pointer conflict resolved (both options), validation failure stops the pipeline with exit 2. Details in `docs/dvc-runbook.md`.
- Not run: Windows/PowerShell; the real UCI download; S3 against real AWS; any DagsHub or S3 DVC remote.

## Known gaps
- Real-data thresholds (`validation.ranges`, `target_rate_bounds`) are set from public knowledge of the dataset. Step 2 may suggest tightening them.
- `dvc.lock` is not shipped: it must come from a real-data run on your machine (runbook section 3).
- `dvc[s3]` is not pinned yet; it is added with the S3/DagsHub remote.
- The fairness-only attribute split and Feast timestamp assignment are Cycles 2 and 4.

## Exit checklist
- [x] `ruff` and `black` clean
- [x] All automated tests pass, including real `dvc repro` reproducibility tests
- [x] Validation failure exits 2 and stops `dvc repro`
- [x] Smoke runs cannot touch `data/raw` or `reports/`
- [x] Merge-conflict procedures rehearsed and written down
- [ ] Manual step 1-6 run on your machine, with real UCI data
- [ ] `dvc.lock` committed from the real run, data pushed to a remote
- [ ] Hashir has reviewed the updated `docs/contracts-s1.md`
