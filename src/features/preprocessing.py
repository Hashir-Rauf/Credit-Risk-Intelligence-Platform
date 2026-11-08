"""PLACEHOLDER for @JA-Codex (Phase 2). Not implemented. See docs/HANDOFF-JA-Codex.md.

Spec (phases.md, Phase 2, steps 1-3):
- sklearn ColumnTransformer: scale numeric columns, encode categoricals; fit on TRAIN only.
- Ship the fitted transformer and the model as one sklearn Pipeline.
- Engineered features, each unit-tested: credit utilisation (BILL_AMT/LIMIT_BAL), payment ratio
  (PAY_AMT/BILL_AMT, guard divide-by-zero), max/mean delay over PAY_1..PAY_6, count of
  delinquent months.
- Stratified train/val/test split with the seed from params.yaml; test set untouched until the
  final evaluation.
- Handle the undocumented codes EDUCATION 0/5/6 and MARRIAGE 0 (validation allows them).
- Decide whether to exclude sensitive features (SEX, AGE) and document it in docs/fairness.md.
"""

from __future__ import annotations

import pandas as pd

NUMERIC_COLS: list[str] = []  # TODO(JA-Codex)
CATEGORICAL_COLS: list[str] = []  # TODO(JA-Codex)


def add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    """TODO(JA-Codex): return df plus utilisation, payment ratio, delay stats, delinquency count."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")


def build_preprocessor():
    """TODO(JA-Codex): return an unfitted sklearn ColumnTransformer."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")


def split_data(df: pd.DataFrame, seed: int):
    """TODO(JA-Codex): stratified train/val/test split on `default`; return three DataFrames."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")
