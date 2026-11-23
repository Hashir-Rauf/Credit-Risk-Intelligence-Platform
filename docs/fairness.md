# Fairness report (Phase 2)

Source: `reports/fairness.json` (test split, model `random_forest`, threshold 0.5, groups under n=50 excluded).
Sensitive attributes (SEX, MARRIAGE, AGE) are **not model inputs** (`sensitive_attributes_in_model: false`); they are used only to audit outcomes.
Checks are reported, not enforced (`enforced: false`).

| Attribute | Disparate impact (>=0.8) | TPR gap (<=0.10) | AUC gap (<=0.05) |
|---|---|---|---|
| SEX | 0.938 pass | 0.017 pass | 0.017 pass |
| AGE_BAND | 0.901 pass | **0.128 fail** | **0.083 fail** |
| MARRIAGE | 0.944 pass | 0.039 pass | **0.062 fail** |

## Findings
- **AGE_BAND.** AUC is lowest for 50-59 (0.722, n=356) vs 0.80 for <30 and 30-39. TPR is highest for <30 (0.666) and lowest for 50-59 (0.538): older customers' defaults are caught less often. Group 60+ (n=44) was excluded as too small.
- **MARRIAGE.** The gap is driven by group 3 ("others", n=50, AUC 0.850) against groups 1 and 2 (~0.789). With n=50 this is mostly sampling noise; groups 1 vs 2 differ by 0.002.
- **SEX.** Passes all checks.
- Approval rates are within 0.08 of each other for every attribute; the failures are about ranking quality and recall, not approval rates.

## Ablation: does using the attributes help?
Adding SEX, MARRIAGE and AGE changes test AUC by -0.0003 (threshold 0.005). They add nothing, so excluding them is free. Excluding them also does not close the gaps (AGE_BAND TPR gap 0.103 with them, 0.128 without): other features act as proxies.

## Decision
Ship the model with these results documented. The age AUC/TPR gap is the finding to carry forward: monitor it in Phase 8 (per-group metrics on labelled feedback) and revisit with group-aware thresholds or reweighting if it persists. The 50-59 and MARRIAGE-3 estimates have wide confidence intervals; treat them as indicative.
