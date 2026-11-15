"""Feature engineering, leakage-safe splitting and the feature schema.

    python -m src.features.preprocessing [--params params.yaml] [--smoke]

Design rules (rules.md §4):
- `engineer_features` is **stateless and row-wise**: it uses only the row's own values, never
  global statistics or the target, so it is identical in training and serving.
- Everything *stateful* (scaling, one-hot categories) lives in the sklearn pipeline and is
  fitted on the **train split only**.
- The fitted pipeline starts with `engineer_features`, so the saved model accepts the raw API
  fields directly and training-serving skew cannot happen.
- Sensitive attributes (SEX, MARRIAGE, AGE) are not model inputs unless
  `features.exclude_sensitive_from_model` is false; they stay in the processed files only for
  fairness reporting.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from src.common.cli import stage_main
from src.common.errors import DataValidationError, IngestionError
from src.common.io import read_json, write_json, write_parquet
from src.common.logging_utils import get_logger
from src.common.seed import derive_seed
from src.data.paths import (
    feature_schema_path,
    raw_csv_path,
    split_path,
    validation_report_path,
)
from src.data.schema import BILL_AMT_COLUMNS, PAY_AMT_COLUMNS, PAY_COLUMNS

log = get_logger(__name__)

SCHEMA_VERSION = 1
SPLITS = ("train", "val", "test")

# Fields a client must send to score one customer (no sensitive attributes, no id).
RAW_INPUT_COLUMNS = [
    "LIMIT_BAL",
    "EDUCATION",
    *PAY_COLUMNS,
    *BILL_AMT_COLUMNS,
    *PAY_AMT_COLUMNS,
]

ENGINEERED_NUMERIC = [
    "util_last",
    "util_mean",
    "util_max",
    "pay_ratio_mean",
    "pay_ratio_last",
    "pay_delay_max",
    "pay_delay_mean",
    "months_delayed",
    "bill_change_ratio",
]
ENGINEERED_CATEGORICAL = ["education_grouped"]
ENGINEERED_COLUMNS = [*ENGINEERED_NUMERIC, *ENGINEERED_CATEGORICAL]

SENSITIVE_NUMERIC = ["AGE"]
SENSITIVE_CATEGORICAL = ["SEX", "MARRIAGE"]
_DOCUMENTED_EDUCATION = [1, 2, 3]  # 4 = others; undocumented 0/5/6 are grouped into "others"


# ------------------------------------------------------------------ stateless features


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of `df` with the engineered columns appended. Pure and row-wise."""
    missing = [c for c in RAW_INPUT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing input columns: {missing}")

    out = df.copy()
    limit = out["LIMIT_BAL"].astype("float64").clip(lower=1.0)
    bills = out[BILL_AMT_COLUMNS].astype("float64")
    paid = out[PAY_AMT_COLUMNS].astype("float64")
    status = out[PAY_COLUMNS].astype("float64")

    utilisation = bills.div(limit, axis=0)
    out["util_last"] = utilisation["BILL_AMT1"]
    out["util_mean"] = utilisation.mean(axis=1)
    out["util_max"] = utilisation.max(axis=1)

    # The payment made in month k settles the bill of month k+1 (PAY_AMT1 pays BILL_AMT2).
    owed = bills.iloc[:, 1:].to_numpy()
    made = paid.iloc[:, :5].to_numpy()
    safe_owed = np.where(owed > 0, owed, 1.0)
    ratio = np.clip(np.where(owed > 0, made / safe_owed, 1.0), 0.0, 1.5)  # nothing owed -> 1
    out["pay_ratio_mean"] = ratio.mean(axis=1)
    out["pay_ratio_last"] = ratio[:, 0]

    out["pay_delay_max"] = status.max(axis=1)
    out["pay_delay_mean"] = status.mean(axis=1)
    out["months_delayed"] = (status > 0).sum(axis=1).astype("float64")
    out["bill_change_ratio"] = (bills["BILL_AMT1"] - bills["BILL_AMT6"]) / limit
    out["education_grouped"] = out["EDUCATION"].where(
        out["EDUCATION"].isin(_DOCUMENTED_EDUCATION), 4
    )
    return out


def model_input_columns(params: dict[str, Any]) -> list[str]:
    """Raw columns fed to the fitted pipeline (the API contract for scoring)."""
    columns = list(RAW_INPUT_COLUMNS)
    if not params["features"]["exclude_sensitive_from_model"]:
        columns += [*SENSITIVE_CATEGORICAL, *SENSITIVE_NUMERIC]
    return columns


def _column_groups(params: dict[str, Any]) -> tuple[list[str], list[str]]:
    numeric = ["LIMIT_BAL", *PAY_COLUMNS, *BILL_AMT_COLUMNS, *PAY_AMT_COLUMNS, *ENGINEERED_NUMERIC]
    categorical = list(ENGINEERED_CATEGORICAL)
    if not params["features"]["exclude_sensitive_from_model"]:
        numeric += SENSITIVE_NUMERIC
        categorical += SENSITIVE_CATEGORICAL
    return numeric, categorical


def build_pipeline(params: dict[str, Any], estimator: Any) -> Pipeline:
    """engineer -> scale/one-hot (fit on train only) -> estimator. Unfitted."""
    numeric, categorical = _column_groups(params)
    prep = ColumnTransformer(
        [
            ("numeric", StandardScaler(), numeric),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                categorical,
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )
    prep.set_output(transform="pandas")
    return Pipeline(
        [
            ("engineer", FunctionTransformer(engineer_features, validate=False)),
            ("prep", prep),
            ("model", estimator),
        ]
    )


# ------------------------------------------------------------------ splitting


def split_data(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Stratified train/val/test split, reproducible from params.seed. Done before anything
    is fitted, so no statistic from val/test can reach the model."""
    fractions = params["split"]
    target = params["data"]["target_column"]
    seed = derive_seed(params["seed"], "split")
    held_out = fractions["val"] + fractions["test"]

    train, rest = train_test_split(df, test_size=held_out, stratify=df[target], random_state=seed)
    val, test = train_test_split(
        rest,
        test_size=fractions["test"] / held_out,
        stratify=rest[target],
        random_state=seed,
    )
    return {"train": train.sort_index(), "val": val.sort_index(), "test": test.sort_index()}


# ------------------------------------------------------------------ feature schema


def build_feature_schema(params: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable description of what the scoring API accepts (names, dtypes, bounds).
    Built from the same validation rules as the data checks, so they cannot drift apart."""
    rules = params["validation"]
    ranges, domains = rules["ranges"], rules["domains"]

    def number(name: str, key: str) -> dict[str, Any]:
        return {"name": name, "dtype": "number", "min": ranges[key][0], "max": ranges[key][1]}

    inputs: list[dict[str, Any]] = [
        number("LIMIT_BAL", "LIMIT_BAL"),
        {"name": "EDUCATION", "dtype": "integer", "allowed": domains["EDUCATION"]},
    ]
    inputs += [{**number(c, "PAY"), "dtype": "integer"} for c in PAY_COLUMNS]
    inputs += [number(c, "BILL_AMT") for c in BILL_AMT_COLUMNS]
    inputs += [number(c, "PAY_AMT") for c in PAY_AMT_COLUMNS]

    if not params["features"]["exclude_sensitive_from_model"]:
        inputs += [
            {"name": "SEX", "dtype": "integer", "allowed": domains["SEX"]},
            {"name": "MARRIAGE", "dtype": "integer", "allowed": domains["MARRIAGE"]},
            {**number("AGE", "AGE"), "dtype": "integer"},
        ]
    excluded = (
        []
        if not params["features"]["exclude_sensitive_from_model"]
        else list(params["features"]["sensitive_columns"])
    )
    assert [i["name"] for i in inputs] == model_input_columns(params)

    return {
        "schema_version": SCHEMA_VERSION,
        "target": params["data"]["target_column"],
        "decision_threshold": params["evaluate"]["decision_threshold"],
        "inputs": inputs,
        "engineered_server_side": ENGINEERED_COLUMNS,
        "excluded_sensitive": excluded,
        "note": "Educational model, not a real lending decision.",
    }


# ------------------------------------------------------------------ stage


def _require_validated(params: dict[str, Any], smoke: bool) -> None:
    report_path = validation_report_path(params, smoke)
    if not report_path.is_file():
        raise IngestionError(f"validation report not found: {report_path} (run validation first)")
    report = read_json(report_path)
    if report.get("passed") is not True:
        raise DataValidationError(
            f"{report_path.name} says passed={report.get('passed')!r}", report
        )


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    _require_validated(params, smoke)
    csv = raw_csv_path(params, smoke)
    if not csv.is_file():
        raise IngestionError(f"raw data not found: {csv} (run ingestion first)")

    frame = engineer_features(pd.read_csv(csv))
    splits = split_data(frame, params)
    for name, part in splits.items():
        write_parquet(split_path(params, name, smoke), part.reset_index(drop=True))
    write_json(feature_schema_path(params, smoke), build_feature_schema(params))

    sizes = {name: int(len(part)) for name, part in splits.items()}
    log.info("split sizes: %s", sizes)
    return {"splits": sizes, "feature_schema": str(feature_schema_path(params, smoke))}


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Engineer features and split train/val/test"))
