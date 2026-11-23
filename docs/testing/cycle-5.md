# Cycle 5 testing: fairness, explanations, simulated drift

## Scope
`src/models/fairness.py`, `explain.py`, `drift.py` (new), `promote.py` (fairness rule), `config.py`/`paths.py`, `params.yaml` (`fairness`, `explain`, `simulate`, `promotion.fairness_check`/`max_fairness_regression`), `dvc.yaml` (leaf stages `fairness`, `explain`, `drift`), `requirements.txt` (`shap==0.51.0`), tests.

- **Fairness** (`reports/fairness.json`): per group of SEX, age band (`<30`, `30-39`, ... `60+`) and MARRIAGE: default rate, approval rate, TPR, FPR, precision, AUC, and the gaps between groups. Groups under `fairness.min_group_size` are listed but excluded. Threshold checks (disparate impact >= 0.8, TPR gap <= 0.10, AUC gap <= 0.05) are **reported, not enforced** (`fairness.enforce: false`): you decide what is acceptable after seeing real numbers. An **ablation** refits the same model WITH SEX/MARRIAGE/AGE and reports the AUC difference, so the decision to exclude them is backed by a number.
- **Promotion** now blocks a challenger whose fairness gaps grew by more than `max_fairness_regression` (0.02) versus the champion (both scored on the same test split). With `fairness.enforce: true` it also blocks a challenger that fails the absolute checks.
- **Explanations** (`reports/explanations.json/.png`, `models/explainer_background.parquet`): contributions in log-odds relative to a background sample, `base + sum(contributions) = log-odds` (tested exact). Logistic regression: exact linear contributions. Random forest / boosting: SHAP permutation explainer. `explain_records(model, background, records)` is the serving helper.
- **Simulated drift** (`data/reference/reference.parquet`, `data/simulated/*.parquet`, `reports/drift_scenarios.json`): 4 scenarios (control, inflated limits, payment delays, spending surge) with PSI/KS per feature and an alert when >= 30% of features have PSI >= 0.2. The stage fails if a scenario does not behave as designed. **Everything here is simulated** and labelled so.

## Run
```
pytest -q tests/test_fairness.py tests/test_drift.py tests/test_explain.py   # about 55 s
pytest -q                                    # everything (about 12 min with the DVC integration tests)
python -m src.models.fairness --smoke        # after the smoke chain through evaluate
python -m src.models.explain --smoke
python -m src.models.drift --smoke
dvc repro                                    # now 10 stages
```

## Automated tests (80 new, 486 in total)
| File | Proves |
|---|---|
| `test_fairness.py` (29) | age-band labels and boundaries; group metrics against hand-computed values; undefined rates are `None`, never errors; gaps and disparate-impact; single group has no gaps; small groups excluded; an obviously unfair scorer FAILS the checks; limits are inclusive and come from params; uncomputable gaps are `skipped`, not `passed`; regression logic (tolerance, improvements, unknown attributes); stage report contents, group sizes sum to the test split; ablation arithmetic and wording; byte-identical reruns; the saved model's scores do not change when SEX/AGE are altered; failed checks exit 3 only when enforced and the evidence is still written; smoke isolation |
| `test_drift.py` (25) | PSI zero for identical data, grows with shift, known discrete value, unseen category stays finite, values outside the reference range counted; KS equals SciPy; dataset alert needs the configured share; scenario changes round to ints, clip to validation ranges, never mutate input, only model inputs allowed; batch sampling deterministic; stage: scenarios behave as designed (control has no alert), reference has inputs+score+label and no sensitive columns, shifted batches stay valid data, a scenario that lies about drifting fails the stage but keeps the report, byte-identical reruns, smoke isolation |
| `test_explain.py` (26) | contributions add up to the model's log-odds for LR, random forest and boosting; LR matches a brute-force calculation; an average row has zero contribution; seeded tree explanations; top factors ordered and labelled; sensitive attributes can never be a factor; a clearly late customer is pushed towards risk; `explain_records` validates raw fields; label mapping; background file works with the saved model; PNG written; deterministic; smoke isolation |
| `test_tuning.py` | promotion: fairness regression blocks an otherwise better challenger; failed absolute checks block; clean/missing fairness input does not block; decision JSON records the comparison |
| `test_common.py` | 12 more invalid `fairness`/`explain`/`simulate`/`promotion` values rejected |
| `test_model.py`, `test_data.py` | `dvc.yaml` has the 3 new leaf stages; real `dvc repro` runs all 10 stages and a second run skips all 10 |

## Manual checks (on your machine)
| # | Step | Expected |
|---|---|---|
| 1 | `pip install -r requirements.txt` (adds `shap`, which pulls numba/llvmlite, about 100 MB) | installs on Python 3.11 |
| 2 | **Real data:** `dvc repro` | 10 stages; `reports/fairness.json`, `explanations.json/.png`, `drift_scenarios.json` appear |
| 3 | Read `reports/fairness.json`: `checks`, `failed_checks`, `ablation.conclusion` | **Send these to me.** Real UCI numbers decide whether to enforce and what to write in the REPORT |
| 4 | Open `reports/explanations.png` | top factors should be payment-delay and utilisation features; if SEX/AGE-like names appear, something is wrong |
| 5 | `reports/drift_scenarios.json`: `all_match_expectation` | `true`; `no_drift` no alert, `payment_delays` and `spending_surge` alert |
| 6 | If you changed the champion algorithm to RF/HGB: time `explain` | RF is about 0.17 s per explained row |

## Recorded results (what was actually run)
- Linux, **Python 3.11, pandas 2.3.3**, Feast 0.67, MLflow 3.18, Optuna 5.0, SHAP 0.51.0, DVC 3.67.1.
- `ruff` and `black` clean. Full suite: **486 tests**; all passed. One test (`test_better_challenger_replaces_the_champion`) correctly started failing because the new fairness rule blocked the synthetic challenger, whose group gaps moved by more than 0.02 on tiny data. It now disables `fairness_check`, since it tests the AUC rules; the fairness rule has its own tests.
- Smoke runs of all three stages on synthetic data: no failed fairness checks; ablation AUC change -0.0008 (not meaningful on synthetic data); no_drift 0% drifted, payment_delays and spending_surge alert.
- Mutation checks, all caught: disparate-impact limit made exclusive; PSI floor for empty bins removed; LR contribution computed without subtracting the background mean. Equivalent mutant accepted: `>` vs `>=` in the fairness-regression comparison (a 1e-12 epsilon makes them the same).
- Not run: Windows, real UCI data (no real fairness or drift numbers yet), SHAP on a tuned RF/HGB through the full `dvc repro`.

## Known gaps
- Synthetic data has no real group differences; the fairness code is proven on constructed cases, not on UCI. Expect real gaps (age and marital status correlate with repayment behaviour).
- Fairness is group-level outcome parity on a single test split; small groups give noisy estimates (listed and excluded below `min_group_size`). It does not prove individual fairness or absence of proxies.
- SHAP explanations describe the model, not real-world causes. They are relative to a 50-row background, so absolute values depend on that sample (the background file is saved so serving uses the same one).
- Drift detection is a lightweight PSI/KS implementation, not Evidently itself. The reference and batch parquet files are the contract Hashir's Evidently job can read.
- The 0.75 gate and 0.8/0.10/0.05 fairness limits are defaults, not findings.

## Exit checklist
- [x] `ruff` / `black` clean, all automated tests pass
- [x] Contributions provably additive; sensitive attributes never appear as factors
- [x] Drift scenarios fail loudly if they stop behaving as designed
- [x] Promotion blocks fairness regressions (unit-tested and mutation-checked)
- [ ] Manual steps 1-6 on real data; send `reports/fairness.json`
- [ ] Decide whether to set `fairness.enforce: true` after seeing real numbers
