# Resolving DVC merge conflicts

Rehearsed on 2026-11-08. The full terminal transcript is in
[`evidence/phase-1/merge_conflict.log`](evidence/phase-1/merge_conflict.log).

## Why conflicts happen
`dvc.lock` records, per stage, the exact params, input hashes and output hashes. If two branches
change the data (or anything upstream of it), both rewrite the same lines of `dvc.lock`
(`md5`, `size`, params), and git cannot merge them. The same happens to generated metrics files
such as `reports/data_validation.json`, and to `params.yaml` when both edit adjacent lines.

## What we did
Base: data v1 (30,000 rows). Two branches, each changing the data a different way:

| Branch | Change | Rows |
|---|---|---|
| `exp/dedupe` | `data.drop_duplicate_rows: true` | 29,965 |
| `exp/clean-education` | `data.drop_unknown_education: true` | 29,655 |

`git merge exp/clean-education` into `exp/dedupe` failed with conflicts in **`dvc.lock`**,
**`params.yaml`** and **`reports/data_validation.json`**.

## Procedure
1. **Hand-written files (`params.yaml`): keep both changes.** Remove the markers so both
   `drop_duplicate_rows: true` and `drop_unknown_education: true` remain.
2. **Generated files (`dvc.lock`, `reports/*.json`): never merge by hand.** Take either side
   (`git checkout --ours dvc.lock reports/data_validation.json`). Hand-merged hashes are wrong:
   they would describe data that was never produced.
3. **Regenerate:** `dvc repro`. DVC rebuilds the data from the merged params and rewrites
   `dvc.lock` and the report (result: 29,620 rows, validation passed).
4. **Check:** no `<<<<<<<` markers left; `dvc status` says up to date.
5. **Share the data:** `dvc push -r s3` and `dvc push -r dagshub`, so teammates can `dvc pull` it.
6. **Commit the merge** (`git add -A && git commit`).

## Rules that prevent most pain
- Only one person edits `dvc.yaml` at a time (CODEOWNERS: `@Hashir-Rauf`).
- Always `dvc push` before opening a PR that changes `dvc.lock`; otherwise reviewers get missing-cache errors.
- After any merge or checkout, run `dvc checkout` so the working data matches `dvc.lock`.
- If `dvc repro` fails on a file full of conflict markers, resolve the markers first (step 2).
