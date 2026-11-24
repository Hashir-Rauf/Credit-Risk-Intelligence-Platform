# Phase 2 review (model, data handling, reproducibility)

Reviewer: Hashir-Rauf (with AI assistance), 2026-11-23. Scope: everything JA-Codex merged in PRs #4 and #7
(`main@273e853`). Method: read the code, then **independently recomputed** the results from the committed
artifacts and **retrained from raw data on a different machine**. Raw evidence:
[`docs/evidence/phase-3/`](../evidence/phase-3/README.md).

## Verdict
The modelling work is sound and unusually well documented. Results are real and reproducible. One issue needs a
decision **before the Phase 4 API contract is frozen** (probability calibration); the rest are small or already fixed.

## What was verified (all passed)
| Check | Result |
|---|---|
| Split | 21,000 / 4,500 / 4,500 = 30,000 rows; no ID shared between any two splits; default rate 22.1% in every split (stratified) |
| Leakage by construction | Features are row-wise and stateless; scaler and encoder are fitted on train only; the test split is never read by `tune` (`tune.py:221`) |
| Leakage by data | 6 of 4,500 test rows have an exact twin (features and label) in train. Negligible (0.13%) |
| Sensitive attributes | `SEX`, `MARRIAGE`, `AGE`, `ID`, target are not among the 20 model inputs |
| Reported metrics | ROC-AUC 0.7883, PR-AUC 0.5782, KS 0.4401, Brier 0.1667, precision 0.509, recall 0.600, accuracy 0.784 recomputed from `models/model.joblib` + `test.parquet`: **identical to 4 decimals (difference 0)** |
| Reproducibility | Retrained from raw data (`preprocess, feast, tune, train, evaluate`) on Windows / Python 3.13 vs the authors' environment: same winner (random forest), same val AUC 0.7922, same test AUC 0.7883. Only difference: validation log-loss differs by 1e-16 |
| What drives the model | Repayment status features (`PAY_1`, `pay_delay_max`, `pay_delay_mean`, `months_delayed`) lead, which is what credit-risk practice predicts. No suspicious single-feature dominance |
| Registry | DagsHub MLflow holds `credit-risk-default-model` v1 with alias `champion` (READY) |
| Honesty of docs | Fairness failures and the 0.75 to 0.77 gate change are stated openly with reasons |

## Findings

### 1. Predicted probabilities are not calibrated (needs a decision) - owner: JA-Codex
The random forest uses `class_weight=balanced_subsample`, which inflates scores. On the test split:
mean predicted probability **0.401** vs observed default rate **0.221**.

| Score quintile | Mean predicted | Actually defaulted |
|---|---|---|
| 1 (lowest) | 0.164 | 0.054 |
| 2 | 0.263 | 0.096 |
| 3 | 0.352 | 0.157 |
| 4 | 0.469 | 0.218 |
| 5 (highest) | 0.756 | 0.582 |

Ranking is good (that is what ROC-AUC measures), but the numbers are not probabilities: a customer shown "76% chance
of default" really defaults about 58% of the time, and "16%" is really about 5%. The model card and the planned
API/UI (`/predict` returns a probability, the UI shows a gauge and risk bands) would present misleading numbers.

Options, in order of preference:
1. Calibrate on the validation split (`CalibratedClassifierCV(method="isotonic", cv="prefit")` or Platt scaling) as
   the last pipeline step, re-run `evaluate`, and add reliability numbers (ECE or the table above) to the model card.
   Ranking metrics stay about the same; the threshold needs re-picking.
2. Keep the model and rename the output in the API/UI/docs to a **risk score** (not a probability), with a plain note.
3. Train without class weights (calibrated by default) and rely on the threshold for the imbalance.

### 2. DVC lock hygiene (fixed) - owner: Hashir-Rauf
`dvc status` was never clean on a fresh clone of `main`:
- `dvc.lock` hashed `src/common` as 14 files and `feature_repo` as 5; Git tracks 7 and 4. The extras were compiled
  `__pycache__/*.pyc` files from the author's machine, hashed into the directory dependency.
- `reports/data_validation.json` was hashed with Windows CRLF newlines (110 bytes), but Git stores LF (103 bytes).

Fix (branch `fix/dvc-hash-hygiene`): `.dvcignore` now ignores bytecode, the validation report is written with LF on
every OS (with a regression test), and the dependency hashes were re-recorded with `dvc commit`. No model or report
output changed. After the fix `dvc status` reports "Data and pipelines are up to date".
Rule going forward: **never commit a `dvc.lock` produced with `__pycache__` present and `.dvcignore` missing**, and
write DVC-tracked text files with explicit `\n`.

### 3. Fairness gaps exist and are not enforced - owner: both
Documented honestly (`docs/fairness.md`): age-band TPR gap 0.128 (limit 0.10) and AUC gap 0.083 (limit 0.05);
marriage AUC gap 0.062 (driven by a 50-row group). The checks run with `enforced: false`, so the quality gate and the
promotion rule cannot block on them. That is acceptable for a coursework model, but the Phase 8 retrain decision
must not silently regress them. Suggest making "no worse than the champion on these gaps" an enforced promotion
condition (the promotion report already has a `fairness` section for this).

### 4. The gate margin is thin but defensible - owner: JA-Codex
The gate is ROC-AUC >= 0.77 and the champion scores 0.788. A 1,000-sample bootstrap of the 4,500-row test split gives a
95% interval of [0.772, 0.804]; 1.5% of resamples fall below 0.77. Sampling noise alone rarely fails the gate, but a
retrain with a different seed can move the score by a similar amount. Keep 0.77; do not lower it to make a run pass.

### 5. Minor
- Accuracy (0.784) barely beats always predicting "no default" (0.779). Expected at 22% prevalence; make sure every
  report and the UI lead with ROC-AUC, PR-AUC and recall, never accuracy.
- `build_feature_schema` uses `assert` in non-test code (bandit B101, low). Harmless, but it is stripped under
  `python -O`; a plain `raise` is safer.
- The 35 duplicate rows (excluding ID) are kept in the data. 6 of them cross the train/test boundary (see above).

## Recommended next actions
| # | Action | Owner | Before |
|---|---|---|---|
| 1 | Decide calibrate vs relabel as "risk score" (finding 1) | JA-Codex, agree with Hashir | Phase 4 API contract |
| 2 | Merge `fix/dvc-hash-hygiene` | Hashir | next PR touching `dvc.lock` |
| 3 | Make fairness non-regression an enforced promotion condition | JA-Codex | Phase 8 |
