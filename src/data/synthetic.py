"""Synthetic stand-in for the UCI credit-card data.

FOR DEMOS AND TESTS ONLY. It has the real file's shape, dtypes and quirks (undocumented
EDUCATION/MARRIAGE/PAY codes, ~22% default rate, a learnable PAY_1 signal) so the pipeline
can be exercised without network access or DVC credentials. Never use it for reported
metrics, the quality-gate baseline or the model card.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.schema import (
    BILL_AMT_COLUMNS,
    PAY_AMT_COLUMNS,
    PAY_COLUMNS,
)

TARGET_RATE = 0.22


def _choice(rng: np.random.Generator, values: list[int], weights: list[float], n: int):
    p = np.asarray(weights, dtype=float)
    return rng.choice(values, size=n, p=p / p.sum())


def make_synthetic_credit_data(
    n_rows: int,
    seed: int,
    *,
    id_column: str = "ID",
    target_column: str = "default_next_month",
) -> pd.DataFrame:
    """Deterministic UCI-shaped DataFrame (canonical column names, int64 everywhere)."""
    rng = np.random.default_rng(seed)

    limit = np.clip(np.round(rng.lognormal(11.8, 0.8, n_rows), -4), 10_000, 1_000_000)
    sex = _choice(rng, [1, 2], [0.4, 0.6], n_rows)
    education = _choice(rng, [1, 2, 3, 4, 0, 5, 6], [35, 47, 16, 0.5, 0.05, 1.0, 0.2], n_rows)
    marriage = _choice(rng, [1, 2, 3, 0], [46, 53, 1.0, 0.2], n_rows)
    age = np.clip(np.round(21 + rng.gamma(2.5, 6.0, n_rows)), 21, 79)

    # Latent delinquency propensity drives repayment status, which drives default.
    propensity = rng.normal(0, 1, n_rows)
    pay: dict[str, np.ndarray] = {}
    for i, name in enumerate(PAY_COLUMNS):
        noise = rng.normal(0, 0.6, n_rows)
        raw = propensity * 1.1 + noise - 0.6 - 0.05 * i
        status = np.where(
            raw < -1.2, -2, np.where(raw < -0.6, -1, np.where(raw < 0.5, 0, np.ceil(raw)))
        )
        pay[name] = np.clip(status, -2, 8).astype(np.int64)

    bills: dict[str, np.ndarray] = {}
    payments: dict[str, np.ndarray] = {}
    utilisation = np.clip(rng.beta(2, 3, n_rows) + 0.1 * propensity, 0.0, 1.2)
    for b_name, p_name in zip(BILL_AMT_COLUMNS, PAY_AMT_COLUMNS, strict=True):
        jitter = np.clip(utilisation + rng.normal(0, 0.08, n_rows), -0.05, 1.3)
        bill = np.round(limit * jitter)
        bills[b_name] = bill.astype(np.int64)
        share = np.clip(rng.beta(1.2, 4, n_rows) - 0.05 * propensity, 0.0, 1.0)
        payments[p_name] = np.round(np.maximum(bill, 0) * share).astype(np.int64)

    logit = (
        0.75 * np.clip(pay["PAY_1"], -2, 8)
        + 0.25 * np.clip(pay["PAY_2"], -2, 8)
        + 1.0 * (utilisation - 0.5)
        - 0.4 * np.log(limit / 150_000)
        + rng.normal(0, 0.5, n_rows)
    )
    lo, hi = -15.0, 15.0
    for _ in range(40):  # bisection: pick the intercept that gives the target default rate
        mid = (lo + hi) / 2
        rate = float(np.mean(1 / (1 + np.exp(-(logit + mid)))))
        lo, hi = (mid, hi) if rate < TARGET_RATE else (lo, mid)
    prob = 1 / (1 + np.exp(-(logit + (lo + hi) / 2)))
    target = (rng.random(n_rows) < prob).astype(np.int64)

    frame = {
        id_column: np.arange(1, n_rows + 1, dtype=np.int64),
        "LIMIT_BAL": limit.astype(np.int64),
        "SEX": sex.astype(np.int64),
        "EDUCATION": education.astype(np.int64),
        "MARRIAGE": marriage.astype(np.int64),
        "AGE": age.astype(np.int64),
        **pay,
        **bills,
        **payments,
        target_column: target,
    }
    return pd.DataFrame(frame)
