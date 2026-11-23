# Feature repo (Feast)

Owner: S1 (`.github/CODEOWNERS`). Definitions live in `src/features/feast_features.py` so the pipeline,
tests and the `feast` CLI share one source; `feature_definitions.py` only re-exports them.

| Feature view | Columns |
|---|---|
| `payment_history` | `PAY_1` .. `PAY_6` |
| `billing` | `BILL_AMT1` .. `BILL_AMT6`, `PAY_AMT1` .. `PAY_AMT6` |
| `account` | `LIMIT_BAL`, `EDUCATION` |

Entity: `customer_id` (UCI `ID`). `SEX`, `MARRIAGE`, `AGE` are never stored. Engineered features are computed
inside the model pipeline, not stored, so training and serving cannot drift apart.

**Timestamps are simulated**: UCI has no timestamps, so each customer gets a deterministic snapshot time in the
window set by `params.yaml` `feast.*`. Never present anything derived from them as real time behaviour.

Build and check everything: `python -m src.features.feast_features` (also the `feast` stage of `dvc repro`).
Serving: `src.features.feast_features.get_online_features(params, [customer_id, ...])`.
