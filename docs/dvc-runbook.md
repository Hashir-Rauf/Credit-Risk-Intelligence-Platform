# DVC runbook (data versioning, reproducibility, merge conflicts)

Owner of `dvc.yaml`/`dvc.lock`/`.dvc/config`: `@Hashir-Rauf` (only one person edits `dvc.yaml`, see rules.md §3). Commands are identical in PowerShell and bash unless noted. Ported from JA-Codex's PR #3 and adapted to `main` (2026-11-15). Sections marked **verified** were run with DVC 3.67.1; the real S3 and DagsHub remotes are configured and exercised (see `docs/evidence/phase-1/`).

## 1. How data versioning works here
- `dvc.yaml` defines the stages `ingest -> validate` (later `preprocess -> train -> evaluate`). `params.yaml` feeds them.
- The **data itself** (`data/raw/credit_default.csv`) is cached by DVC and pushed to the DVC remote. It is never in git (`.gitignore`).
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

Team remotes are already in the committed `.dvc/config` (no credentials): `s3` (default) and `dagshub`. Put your own credentials in the git-ignored `.dvc/config.local`:
```
dvc remote modify --local s3 profile <your-aws-profile>        # aws login; also needs botocore[crt] (in requirements)
dvc remote modify --local dagshub user <dagshub-user>
dvc remote modify --local dagshub password <dagshub-token>
dvc pull -r dagshub                                            # or: dvc pull  (S3)
```

## 3. First run on real data (do this once)
```
python -m src.data.ingestion        # downloads the UCI zip, writes data/raw/credit_default.csv
python -m src.data.validation       # expect exit code 0
echo $LASTEXITCODE                  # bash: echo $?
dvc repro                           # writes dvc.lock
git add dvc.lock reports/ ; git commit -m "Add data v1: UCI default of credit card clients"
dvc push
```
Expected on the real file: 30,000 rows, 25 columns, default rate about 0.22 (`reports/data_validation.json` shows `passed: true`). The undocumented UCI codes (`EDUCATION` 0/5/6, `MARRIAGE` 0) are allowed by the schema and handled in preprocessing. If validation fails, read `reports/data_validation.json`: it names the column, the failed check, the count and up to 3 offending values. In normal use skip this section and run `dvc pull`.

## 4. Reproducibility demo (spec 3.2) - verified
Shows "retrieve an earlier code version and its matching dataset".
```
git log --oneline                           # pick the old commit, e.g. a1b2c3d
git checkout a1b2c3d                        # code, params.yaml, dvc.lock go back
dvc checkout                                # data/raw goes back to the matching version
grep -A3 'path: data/raw/credit_default.csv' dvc.lock      # md5 recorded for that commit
sha256sum data/raw/credit_default.csv  # PowerShell: Get-FileHash data\raw\credit_default.csv
git checkout main ; dvc checkout            # come back
dvc pull                                    # fresh clone: fetch missing data from the remote
```
Automated version: `scripts/repro_demo.sh <old-sha>` (checks the md5 recorded in `dvc.lock` against the restored file, then returns). Transcript: `docs/evidence/phase-1/repro_demo.log`.

Other behaviours worth showing: a second `dvc repro` skips every stage ("didn't change, skipping"); a failing validation stops `dvc repro` before preprocessing (see `docs/evidence/phase-1/validation_corrupt_vs_good.log`).

## 5. Merge conflicts (spec 3.2) - verified
Rule: **never hand-edit hashes in `dvc.lock` or `.dvc` files.** Resolve the human-edited file, then let DVC regenerate the rest.

### 5A. Pipeline conflict (`params.yaml`, `dvc.lock`, `reports/*.json`)
Full rehearsed transcript and explanation: [dvc-merge-conflicts.md](dvc-merge-conflicts.md).
Two branches changed the data parameters and both re-ran the pipeline.
```
git merge branch-a                      # CONFLICT in dvc.lock, params.yaml, reports/*.json
# 1. edit params.yaml: keep ONE value for each conflicting key, delete the markers
# 2. clear markers in generated files, any side will do, they are regenerated next:
git checkout --ours dvc.lock reports/data_validation.json
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
