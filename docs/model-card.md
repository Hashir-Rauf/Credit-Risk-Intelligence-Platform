---
language: en
license: cc-by-4.0   # same as the UCI data; confirm the code licence separately
tags: [tabular-classification, credit-risk, scikit-learn, course-project]
---
# Model card: credit-card default predictor

Course project (MLOps). **Not a production credit decision system.** Values marked `<fill>` come from the registry/promote step on the real run.

## Model details
- Task: binary classification, probability that a customer defaults next month.
- Algorithm: random forest (`n_estimators=200, max_depth=9, min_samples_leaf=2, max_features=sqrt, class_weight=balanced_subsample`), chosen by Optuna over 3 families (logistic regression, random forest, HistGradientBoosting). See `docs/model-selection.md`.
- Packaging: one scikit-learn `Pipeline` (feature engineering, scaling and encoding, estimator). 20 raw inputs, listed in `reports/feature_schema.json`.
- Registry: `credit-risk-default-model`, version `<fill>`, alias `champion`. Git SHA `<fill>`. DVC data hash `<fill>`. MLflow run `<fill>`.
- Libraries: scikit-learn 1.9.1, Optuna 5.0.0, SHAP 0.51.0 (see `requirements.lock.txt`).

## Intended use
Demonstrating an end-to-end MLOps pipeline: versioned data, tracked experiments, gated promotion, serving, monitoring. Out of scope: real lending, pricing or any decision about a real person.

## Data
UCI "Default of Credit Card Clients" (30,000 rows, Taiwan, 2005; CC BY 4.0), default rate 22.1%. Stratified split 70/15/15 (21,000 / 4,500 / 4,500), seed 42. Tuning and selection use the validation split; the test split is read only for the final evaluation. The data is old, from one country and one bank, so it does not represent current or other populations. Feature-store timestamps are **simulated** (the dataset has none).

## Metrics (test split, threshold 0.5)
| ROC-AUC | PR-AUC | KS | Accuracy | Precision | Recall | F1 | Brier |
|---|---|---|---|---|---|---|---|
| 0.7883 | 0.578 | 0.440 | 0.784 | 0.509 | 0.600 | 0.551 | 0.167 |

Baseline logistic regression: ROC-AUC 0.7675. Quality gate: ROC-AUC >= 0.77. Precision is about 0.5 at this threshold: half of the flagged customers do not default. Choose the threshold for the business cost, not the default 0.5.

## Fairness
`SEX`, `MARRIAGE` and `AGE` are not model inputs; they are only used to audit outcomes (`docs/fairness.md`). Test split results: sex passes all checks; age band fails the TPR gap (0.128, limit 0.10) and AUC gap (0.083, limit 0.05), with the lowest recall for customers aged 50-59; marital status fails the AUC gap (0.062) but that comes from a group of only 50 customers. Customers aged 60+ (n=44) are too few to evaluate. Excluding the attributes costs no measurable accuracy but does not remove the gaps (other features act as proxies).

## Explainability
Per-prediction contributions in log-odds (exact for logistic regression, SHAP permutation for tree models), returned with the top factors. They explain the model, not real-world causes. Global importance: `reports/explanations.json` and `.png`.

## Limitations and risks
- Single snapshot, no concept of time; drift handling is demonstrated on **simulated** scenarios only.
- Group fairness on one test split with small groups; not a proof of individual fairness.
- Probabilities are not calibrated for a different population; recalibrate before any use on other data.
- Hyperparameter search used one validation split (plus a train-CV stability check), so the validation AUC is slightly optimistic; the test AUC is the figure to quote.

## Reproduce
`dvc pull -r dagshub && dvc repro` (10 stages), then `python -m src.models.registry` and `python -m src.models.promote`.
