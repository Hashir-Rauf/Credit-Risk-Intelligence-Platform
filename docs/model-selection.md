# Model selection (Phase 2)

Data: UCI credit-card default, 30,000 rows, split 21,000 / 4,500 / 4,500 (train / val / test), seed 42.
Selection uses **validation ROC-AUC only**; the test split is read once, by `train`/`evaluate`, after the choice.

| Family | Best val ROC-AUC | Trials |
|---|---|---|
| logistic_regression | 0.7634 | 20 |
| hist_gradient_boosting | 0.7921 | 20 |
| random_forest (selected) | 0.7922 | 20 |

**Selected: random_forest** (`n_estimators=200, max_depth=9, min_samples_leaf=2, max_features=sqrt, class_weight=balanced_subsample`).

## Honest reading
- The two tree ensembles are tied: random_forest leads HistGradientBoosting by 0.0001 val AUC, far inside trial-to-trial noise. The choice follows the stated rule (highest val AUC), not a real superiority.
- Both beat logistic regression by ~0.029 val AUC, so non-linear interactions matter. That gap is real.
- Tuning has no cross-validation (single validation split, 20 trials per family), so the best value is slightly optimistic. Test AUC is the unbiased figure.

## Test results (decision threshold 0.5)
ROC-AUC 0.7883 | PR-AUC 0.578 | KS 0.440 | accuracy 0.784 | precision 0.509 | recall 0.600 | F1 0.551 | Brier 0.167.
Baseline LR test ROC-AUC: 0.7675. Gain from tuning/model choice: +0.021.
Val-to-test drop is 0.004, so there is no sign of overfitting to the validation split.

## Quality gate
`gate.roc_auc_min` raised 0.75 -> 0.77: above the LR baseline and ~0.018 below the champion, so a regression to baseline-level models is blocked while normal run-to-run variation passes.

## Known deviations from phases.md
Third family is HistGradientBoosting (no XGBoost/LightGBM dependency); aliases are `challenger`/`champion` (MLflow stages are deprecated).
