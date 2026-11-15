"""Canonical column names of the raw credit-card data (after ingestion renames them).

The pandera schema that validates the raw file lives in src/data/validation.py.
"""

from __future__ import annotations

from typing import Any

PAY_COLUMNS = [f"PAY_{i}" for i in range(1, 7)]
BILL_AMT_COLUMNS = [f"BILL_AMT{i}" for i in range(1, 7)]
PAY_AMT_COLUMNS = [f"PAY_AMT{i}" for i in range(1, 7)]
DEMOGRAPHIC_COLUMNS = ["SEX", "EDUCATION", "MARRIAGE", "AGE"]


def canonical_columns(params: dict[str, Any]) -> list[str]:
    """Column order of the raw CSV: id, attributes, history, target."""
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
