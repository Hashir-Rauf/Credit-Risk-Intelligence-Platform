# Model selection (Phase 2)

The verified UCI dataset contains 30,000 records. With seed 42, the stratified split contains 21,000 training, 4,500 validation, and 4,500 test records. Optuna ran 20 trials per family. The three-fold training CV results describe the stability of each family's selected hyperparameters. The selection rule uses validation ROC-AUC and a 0.002 simplicity tie-break.

| Family | Best val ROC-AUC | Train CV ROC-AUC (mean +/- std) | Trials | Selected |
|---|---|---|---|---|
| logistic_regression | 0.7634 | 0.7557 +/- 0.0077 (3 folds) | 20 |  |
| random_forest | 0.7922 | 0.7813 +/- 0.0100 (3 folds) | 20 | yes |
| hist_gradient_boosting | 0.7925 | 0.7834 +/- 0.0044 (3 folds) | 20 |  |

**Selected: random_forest.** Reason: random_forest is within 0.002 validation ROC-AUC of hist_gradient_boosting (0.7922 vs 0.7925) and is the simpler model.

Hyperparameters: `n_estimators=200, max_depth=9, min_samples_leaf=2, class_weight=balanced_subsample, n_jobs=-1, max_features=sqrt`

The same-seed repeat reproduced every reported test metric. Only validation log loss differed, by 1.11e-16, within the configured tolerance of 1e-9.

## Test results (decision threshold 0.5)
ROC-AUC 0.7883 | PR-AUC 0.578 | KS 0.440 | accuracy 0.784 | precision 0.509 | recall 0.600 | F1 0.551 | Brier 0.167.
Baseline LR test ROC-AUC: 0.7675. Gain from tuning/model choice: +0.021.
Val-to-test drop is 0.004, so there is no sign of overfitting to the validation split.

## Quality gate
`gate.roc_auc_min` raised 0.75 -> 0.77: above the LR baseline and ~0.018 below the champion, so a regression to baseline-level models is blocked while normal run-to-run variation passes.

## Known deviations from phases.md
Third family is HistGradientBoosting (no XGBoost/LightGBM dependency); aliases are `challenger`/`champion` (MLflow stages are deprecated).
