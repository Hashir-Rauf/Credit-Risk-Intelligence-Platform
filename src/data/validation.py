"""Schema and quality checks for the raw credit-default data.

`validate(df)` returns a structured report and raises `DataValidationError` when
any check fails, so a pipeline stage (or Airflow task) stops before bad data
reaches training.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import yaml

log = logging.getLogger(__name__)

TARGET = "default"
PAY_COLS = [f"PAY_{i}" for i in range(1, 7)]
BILL_COLS = [f"BILL_AMT{i}" for i in range(1, 7)]
PAY_AMT_COLS = [f"PAY_AMT{i}" for i in range(1, 7)]


def _int(**kwargs) -> pa.Column:
    return pa.Column(int, nullable=False, **kwargs)


# EDUCATION 0/5/6 and MARRIAGE 0 are undocumented codes present in the source data; they are
# allowed here (handled in preprocessing), not silently dropped.
SCHEMA = pa.DataFrameSchema(
    {
        "customer_id": _int(checks=pa.Check.gt(0), unique=True),
        "LIMIT_BAL": _int(checks=pa.Check.in_range(1, 5_000_000)),
        "SEX": _int(checks=pa.Check.isin([1, 2])),
        "EDUCATION": _int(checks=pa.Check.isin(range(0, 7))),
        "MARRIAGE": _int(checks=pa.Check.isin(range(0, 4))),
        "AGE": _int(checks=pa.Check.in_range(18, 100)),
        **{c: _int(checks=pa.Check.in_range(-2, 9)) for c in PAY_COLS},
        **{c: _int(checks=pa.Check.in_range(-1_000_000, 5_000_000)) for c in BILL_COLS},
        **{c: _int(checks=pa.Check.ge(0)) for c in PAY_AMT_COLS},
        TARGET: _int(checks=pa.Check.isin([0, 1])),
    },
    strict=True,  # unexpected extra columns fail
    ordered=False,
    coerce=False,  # wrong dtypes fail instead of being silently cast
)


class DataValidationError(ValueError):
    def __init__(self, report: dict):
        self.report = report
        lines = [
            f"  - {f['column']}: {f['check']} ({f['count']} rows, e.g. {f['examples']})"
            for f in report["failures"]
        ]
        super().__init__(
            f"Data validation failed with {len(report['failures'])} issue(s):\n" + "\n".join(lines)
        )


def _failures(err: pa.errors.SchemaErrors) -> list[dict]:
    cases = err.failure_cases
    out = []
    for (column, check), grp in cases.groupby(["column", "check"], dropna=False):
        out.append(
            {
                "column": None if pd.isna(column) else str(column),
                "check": str(check),
                "count": int(len(grp)),
                "examples": [str(v) for v in grp["failure_case"].head(3).tolist()],
            }
        )
    return sorted(out, key=lambda f: (str(f["column"]), f["check"]))


def validate(df: pd.DataFrame) -> dict:
    """Validate `df`. Returns the report on success, raises DataValidationError on failure."""
    report: dict = {"passed": True, "n_rows": int(len(df)), "n_columns": int(df.shape[1])}
    failures: list[dict] = []
    try:
        SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as err:
        failures = _failures(err)
    if len(df) == 0:
        failures.append(
            {"column": None, "check": "non-empty dataframe", "count": 0, "examples": []}
        )
    report["failures"] = failures
    report["passed"] = not failures
    if TARGET in df.columns and len(df):
        report["default_rate"] = round(float(df[TARGET].mean()), 4)
    if failures:
        raise DataValidationError(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", default="params.yaml")
    parser.add_argument("--input", help="CSV to validate (default: data.raw_path from params)")
    parser.add_argument("--report", help="where to write the JSON report")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with open(args.params, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)["data"]
    src = args.input or cfg["raw_path"]
    report_path = Path(args.report or cfg["validation_report"])
    df = pd.read_csv(src)
    try:
        report = validate(df)
    except DataValidationError as err:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(err.report, indent=2) + "\n", encoding="utf-8")
        log.error("%s", err)
        sys.exit(1)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info(
        "Validation passed: %s rows, default rate %s", report["n_rows"], report["default_rate"]
    )


if __name__ == "__main__":
    main()
