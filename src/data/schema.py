"""Canonical column names and the Pandera schema for the raw credit-card data."""

from __future__ import annotations

from typing import Any

import pandera.pandas as pa

PAY_COLUMNS = [f"PAY_{i}" for i in range(1, 7)]
BILL_AMT_COLUMNS = [f"BILL_AMT{i}" for i in range(1, 7)]
PAY_AMT_COLUMNS = [f"PAY_AMT{i}" for i in range(1, 7)]
DEMOGRAPHIC_COLUMNS = ["SEX", "EDUCATION", "MARRIAGE", "AGE"]


def canonical_columns(params: dict[str, Any]) -> list[str]:
    """Column order of data/raw/<raw_filename>: id, attributes, history, target."""
    data = params["data"]
    return [
        data["id_column"],
        "LIMIT_BAL",
        *DEMOGRAPHIC_COLUMNS,
        *PAY_COLUMNS,
        *BILL_AMT_COLUMNS,
        *PAY_AMT_COLUMNS,
        data["target_column"],
    ]


def build_schema(params: dict[str, Any]) -> pa.DataFrameSchema:
    """Strict schema: unknown or missing columns, wrong types, nulls, duplicate ids and
    out-of-domain values are all failures. Bounds and domains come from params.yaml."""
    rules = params["validation"]
    domains, ranges = rules["domains"], rules["ranges"]
    data = params["data"]

    def in_range(key: str) -> pa.Check:
        lo, hi = ranges[key]
        return pa.Check.in_range(lo, hi)

    columns: dict[str, pa.Column] = {
        data["id_column"]: pa.Column(int, pa.Check.ge(1), nullable=False, unique=True),
        "LIMIT_BAL": pa.Column(float, in_range("LIMIT_BAL"), coerce=True, nullable=False),
        "SEX": pa.Column(int, pa.Check.isin(domains["SEX"]), nullable=False),
        "EDUCATION": pa.Column(int, pa.Check.isin(domains["EDUCATION"]), nullable=False),
        "MARRIAGE": pa.Column(int, pa.Check.isin(domains["MARRIAGE"]), nullable=False),
        "AGE": pa.Column(int, in_range("AGE"), nullable=False),
        data["target_column"]: pa.Column(int, pa.Check.isin([0, 1]), nullable=False),
    }
    for name in PAY_COLUMNS:
        columns[name] = pa.Column(int, in_range("PAY"), nullable=False)
    for name in BILL_AMT_COLUMNS:
        columns[name] = pa.Column(float, in_range("BILL_AMT"), coerce=True, nullable=False)
    for name in PAY_AMT_COLUMNS:
        columns[name] = pa.Column(float, in_range("PAY_AMT"), coerce=True, nullable=False)

    return pa.DataFrameSchema(columns, strict=True, ordered=False)
