# Model comparison

Metric: ROC-AUC on the **validation** split (models fitted on train only; test split untouched). Trials per family: 20. Seed: 42.

| Family | Best val ROC-AUC | Train CV ROC-AUC (mean +/- std) | Trials | Selected |
|---|---|---|---|---|
| logistic_regression | 0.7634 | 0.7557 +/- 0.0077 (3 folds) | 20 |  |
| random_forest | 0.7922 | 0.7813 +/- 0.0100 (3 folds) | 20 | yes |
| hist_gradient_boosting | 0.7925 | 0.7834 +/- 0.0044 (3 folds) | 20 |  |

**Selected: random_forest.** Reason: random_forest is within 0.002 validation ROC-AUC of hist_gradient_boosting (0.7922 vs 0.7925) and is the simpler model.

Hyperparameters: `n_estimators=200, max_depth=9, min_samples_leaf=2, class_weight=balanced_subsample, n_jobs=-1, max_features=sqrt`
