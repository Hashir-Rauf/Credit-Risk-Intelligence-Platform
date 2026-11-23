# Phase 2 evidence

The approved model is `models:/credit-risk-default-model@champion`, version `1`.
The source commit is `4d7a369f8d8c326daaa12165d6139b596bfbf0c2`.
The DVC raw-data MD5 is `a7e80c905840715d8456fe06d935f981`.

| Evidence | What it shows |
|---|---|
| [Pipeline retry log](dvc-repro-retry.log) | Successful completion after the Windows UTF-8 fix |
| [Repeat training log](repro-train-evaluate.log) | Fresh training and evaluation using the same seed and tuned parameters |
| [First-run metrics](metrics-first-run.json) and [repeat metrics](metrics-repeat-run.json) | All test metrics matched. Only validation log loss differed, by 1.11e-16, within the 1e-9 tolerance. |
| [Feast online sample](feast-online-sample.txt) and [Feast report](../../../reports/feast_report.json) | Online retrieval for three customer IDs and the feature-store quality checks |
| [Model comparison](../../../reports/model_comparison.md) | Three families, 20 trials per family, three-fold CV, and the selection decision |
| [Fairness report](../../../reports/fairness.json) | Report-only failures for age-band TPR gap, age-band AUC gap, and marital-status AUC gap |
| [Registration](../../../reports/registration.json) and [promotion](../../../reports/promotion.json) | Version 1 became the first champion after passing the 0.77 test ROC-AUC gate |

First training run: `4773e7f881034fe4a04beb581870bf63`.
Repeated and promoted training run: `7b0c754e857240b49a8554e4d4bbe92f`.
Test ROC-AUC: `0.7883450182006565`.

## Artifact remotes

- DagsHub: 20 files pushed. DVC confirmed the remote and local cache were in sync. See [remote status](dvc-status-dagshub.txt).
- S3: pending verification by Hashir. The push from Junaid's machine stopped because DVC could not locate AWS credentials.
- Handoff: from a clean checkout of the final Phase 2 commit, run `dvc pull -r dagshub` and `dvc status`. From an authorized AWS environment, run `dvc push -r s3` followed by `dvc status --cloud -r s3` and save the result.
