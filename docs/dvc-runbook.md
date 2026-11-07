# DVC runbook (data versioning, reproducibility, merge conflicts)

Owner: `@JA-Codex` (only one person edits `dvc.yaml`, see rules.md §3). Commands are identical in PowerShell and bash unless noted. Everything marked **verified** was run end to end in a throwaway repo on Linux (DVC 3.67.1, local remote, synthetic data); remote setup for S3/DagsHub is **not** exercised yet (no bucket or DagsHub repo exists).

## 1. How data versioning works here
- `dvc.yaml` defines the stages `ingest -> validate` (later `preprocess -> train -> evaluate`). `params.yaml` feeds them.
- The **data itself** (`data/raw/default_credit_card.csv`) is cached by DVC and pushed to the DVC remote. It is never in git (`.gitignore`).
- The **version pointer** is `dvc.lock` (hashes of every dep, param and output). **Commit `dvc.lock`**: checking out an old git commit gives you the old `dvc.lock`, and `dvc checkout` restores the matching data.
- Small JSON reports (`reports/*.json`) are `cache: false`: they live in git so diffs are readable in PRs.
- No `.dvc` pointer files exist yet because we use pipelines rather than `dvc add`. If you `dvc add` something later, see section 5B.

## 2. One-time setup (each developer)
```
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # bash: source .venv/bin/activate
pip install -r requirements-dev.txt
dvc remote add -d --local localstore ..\dvc-store     # local dev remote, stored in .dvc/config.local (git-ignored)
```
`dvc repro` runs `python -m ...`, so the virtual environment must be active (otherwise the wrong `python` runs).

If you edit a tracked data file by hand, `dvc checkout` refuses to overwrite it ("unsaved files"); use `dvc checkout --force` to discard your edit (verified).

Team remotes (set up once a bucket / DagsHub repo exists; **never** put keys in `.dvc/config`):
- S3: `pip install "dvc[s3]"`, then `dvc remote add -d storage s3://<bucket>/dvc` and `dvc remote modify storage region <region>`. Credentials come from your AWS profile or SSO.
- DagsHub: follow the "Remote" tab of the DagsHub repo; put user and token with `--local` so they land in `.dvc/config.local`.

## 3. First run on real data (do this once)
```
python -m src.data.ingestion        # downloads the UCI zip, writes data/raw/default_credit_card.csv
python -m src.data.validation       # expect exit code 0; warnings are normal (see below)
echo $LASTEXITCODE                  # bash: echo $?
dvc repro                           # writes dvc.lock
git add dvc.lock reports/ ; git commit -m "Add data v1: UCI default of credit card clients"
dvc push
```
Expected on the real file (from the dataset's public description; **verify**): 30,000 rows, 25 columns, default rate about 0.22, unique `ID`, warnings for `EDUCATION` codes 0/5/6, `MARRIAGE` code 0 and `PAY_*` codes -2/0. If validation fails, read `reports/validation_report.json`: it names the column, the failed check, the count and up to 3 offending values. If the Excel read fails, open the `.xls` in Excel, save as CSV, set `data.source: local` and `data.local_path` in `params.yaml`, and tell me the error.

## 4. Reproducibility demo (spec 3.2) - verified
Shows "retrieve an earlier code version and its matching dataset".
```
git log --oneline                           # pick the old commit, e.g. a1b2c3d
git checkout a1b2c3d                        # code, params.yaml, dvc.lock go back
dvc checkout                                # data/raw goes back to the matching version
python -c "import json;print(json.load(open('reports/ingestion_report.json'))['sha256'])"   # matches that commit
sha256sum data/raw/default_credit_card.csv  # PowerShell: Get-FileHash data\raw\default_credit_card.csv
git checkout main ; dvc checkout            # come back
dvc pull                                    # fresh clone: fetch missing data from the remote
```
Automated version: `pytest -m integration tests/test_data.py::TestDvcReproducibility` (changes the seed, proves a new data hash, goes back, proves the old hash returns). Save screenshots of this demo in `docs/evidence/`.

Other behaviours worth showing, all verified: a second `dvc repro` skips both stages ("didn't change, skipping"); changing `seed` re-runs both stages; a failing validation stops `dvc repro` with `exited with 2` (set `validation.min_rows: 99999` to demo it, then revert).

## 5. Merge conflicts (spec 3.2) - verified
Rule: **never hand-edit hashes in `dvc.lock` or `.dvc` files.** Resolve the human-edited file, then let DVC regenerate the rest.

### 5A. Pipeline conflict (`params.yaml`, `dvc.lock`, `reports/*.json`)
Two branches changed `seed` and both re-ran the pipeline.
```
git merge branch-a                      # CONFLICT in dvc.lock, params.yaml, reports/*.json
# 1. edit params.yaml: keep ONE value for each conflicting key, delete the markers
# 2. clear markers in generated files, any side will do, they are regenerated next:
git checkout --ours dvc.lock reports/ingestion_report.json reports/validation_report.json
# 3. regenerate from the resolved params:
dvc repro                               # re-runs the stages, rewrites dvc.lock and reports
git add -A ; git commit                 # merge commit
dvc status                              # "Data and pipelines are up to date."
dvc push
```

### 5B. `.dvc` pointer-file conflict (only if someone uses `dvc add`)
Two branches changed the same tracked file, so `file.csv.dvc` has conflicting `md5:` lines.
```
git merge feat-a                        # CONFLICT in lookup/table.csv.dvc
# Option 1: keep one side
git checkout --theirs lookup/table.csv.dvc ; git add lookup/table.csv.dvc
dvc checkout lookup/table.csv.dvc       # workspace file now matches that pointer
# Option 2: keep both sides' content
#   edit the real file by hand (combine the rows), then:
dvc add lookup/table.csv ; git add -A ; git commit ; dvc push
```
Check afterwards with `dvc status` (should report up to date).

## 6. Evidence to capture (rules.md §10)
Screenshots into `docs/evidence/`: first `dvc repro`, `dvc push`, the section 4 demo, a conflict being resolved (5A or 5B), and a failed validation (exit 2).
