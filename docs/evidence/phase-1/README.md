# Phase 1 evidence (data, DVC, DagsHub)

Captured 2026-11-08 on branch `feat/phase-1-data-dvc`. Commits are authored by Hashir-Rauf.

| Exit criterion / deliverable | Evidence file | Result |
|---|---|---|
| Repro demo restores earlier data byte-for-byte | [repro_demo.log](repro_demo.log) | data v2 (`835412d`, 29,965 rows) restored with matching md5 `22e7b58a...`; back on v1 (30,000 rows, md5 `a7e80c90...`) |
| `dvc dag` | [dvc_dag.txt](dvc_dag.txt) | `ingest -> validate` |
| Both remotes in sync; committed config has no credentials | [dvc_remotes_status.txt](dvc_remotes_status.txt) | `s3` and `dagshub` in sync, pipeline up to date |
| Corrupt data fails with a clear message | [validation_corrupt_vs_good.log](validation_corrupt_vs_good.log), [corrupt_report.json](corrupt_report.json) | corrupt fixture exits 1 with 6 issues; good sample exits 0 |
| Tests and lint | [tests_and_lint.log](tests_and_lint.log) | 23 passed; ruff and black clean |
| Merge-conflict exercise | [merge_conflict.log](merge_conflict.log), [../../dvc-merge-conflicts.md](../../dvc-merge-conflicts.md) | conflict in `dvc.lock`, `params.yaml`, report; resolved with `dvc repro` |
| Git history of the work | [git_history.txt](git_history.txt) | |
| DagsHub status | [dagshub_check.txt](dagshub_check.txt) | mirror repo, DVC remote, MLflow endpoint and data file verified |
| `dvc repro` from a fresh clone after `dvc pull` | checked in a clean copy (outside git, 2026-11-08); same md5 `a7e80c90...`, `dvc status` clean | see summary in `memory.md` |

## Not captured automatically
- **DagsHub data view screenshot** (needs a logged-in browser). The DagsHub repo
  `Hashir-Rauf/Credit-Risk-Intelligence-Platform` mirrors the GitHub repo. Open it, switch the branch
  to `feat/phase-1-data-dvc` (or `main` after the PR merges), open `data/raw`, and save the screenshot
  here as `dagshub_data_view.png`. `credit_default.csv` should show as a DVC file with hash `a7e80c90...`
  (confirmed through the API in `dagshub_check.txt`).
- Branch-protection and budget screenshots (Phase 0 evidence) are taken in the GitHub and AWS consoles.

## Notes
- The merge-conflict branches (`exp/dedupe`, `exp/clean-education`) were local and were deleted
  after the exercise; the transcript above is the record.
- Commit `835412d` is the "data v2" version used by the repro demo; `99261fc` returns to v1.
