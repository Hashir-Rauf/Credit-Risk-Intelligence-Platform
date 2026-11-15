# Phase 1 evidence (data, DVC, DagsHub)

Refreshed 2026-11-15 after PR #3 (JA-Codex's S1 data layer) was merged with the earlier Phase 1 work.
Current pipeline: `ingest -> validate` on `data/raw/default_credit_card.csv` (30,000 rows, sha256
`0bfd903f...`). Evidence from the earlier data layer is kept in [previous-data-layer/](previous-data-layer/).

| Exit criterion / deliverable | Evidence | Result |
|---|---|---|
| Repro demo restores earlier data byte-for-byte | [repro_demo.log](repro_demo.log) | commit `835412d` (older layer, md5 `22e7b58a...`) restored exactly; back on current (md5 `6a4f15ee...`) |
| `dvc dag` | [dvc_dag.txt](dvc_dag.txt) | `ingest -> validate` |
| Remotes configured; committed config has no credentials | [dvc_remotes_status.txt](dvc_remotes_status.txt) | `dagshub` in sync. `s3`: current data not pushed yet (AWS login expired, see below) |
| Corrupt data fails with a clear message | [validation_corrupt_vs_good.log](validation_corrupt_vs_good.log) | 6 injected errors reported, exit code 2; real data passes |
| Tests and lint | [tests_and_lint.log](tests_and_lint.log) | 159 passed, 95% coverage; ruff, black, `pip check` clean |
| DagsHub data view | [dagshub_data_view.png](dagshub_data_view.png), [dagshub_check.txt](dagshub_check.txt) | captured on the earlier data version; see note |
| Merge-conflict exercise | [previous-data-layer/merge_conflict.log](previous-data-layer/merge_conflict.log), [../../dvc-merge-conflicts.md](../../dvc-merge-conflicts.md) | procedure is layer-independent |
| Git history | [git_history.txt](git_history.txt) | |

## Notes
- **S3 push pending:** the `aws login` session expired, so `dvc push -r s3` for the current data could not run.
  Re-run `aws login --region ap-southeast-2 --profile credit-risk`, then `dvc push -r s3`.
- **DagsHub screenshot** shows `credit_default.csv` from the earlier layer. Retake it of
  `data/raw/default_credit_card.csv` after this merge lands on `main`.
- `dagshub_check.txt` and the merge-conflict doc describe the earlier layer's file names and hashes.
- The real UCI file produces warnings, not failures, for undocumented codes (EDUCATION 0/5/6,
  MARRIAGE 0, PAY -2/0) and reports 35 duplicate rows excluding ID (kept).
