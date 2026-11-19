# Model comparison

Metric: ROC-AUC on the **validation** split (models fitted on train only; test split untouched). Trials per family: 20. Seed: 42.

| Family | Best val ROC-AUC | Trials | Selected |
|---|---|---|---|
| logistic_regression | 0.7634 | 20 |  |
| random_forest | 0.7922 | 20 | yes |
| hist_gradient_boosting | 0.7921 | 20 |  |

**Selected: random_forest.** Reason: highest validation ROC-AUC (0.7922).

Hyperparameters: `n_estimators=200, max_depth=9, min_samples_leaf=2, class_weight=balanced_subsample, n_jobs=-1, max_features=sqrt`
