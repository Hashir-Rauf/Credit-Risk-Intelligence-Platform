"""Validate the raw CSV against the strict schema and write a JSON report.

    python -m src.data.validation [--params params.yaml] [--smoke]

Exit code 2 (DataValidationError) stops the pipeline: Airflow must not trigger Kubeflow on bad
data. The report is written in every case (pass or fail) and is deterministic (no timestamps),
so DVC and CI diffs stay meaningful. Values appear in the report only as counts and a few
examples of the *offending* values, never as whole rows.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pandera.pandas as pa

from src.common.cli import stage_main
from src.common.errors import DataValidationError, IngestionError
from src.common.io import write_json
from src.common.logging_utils import get_logger
from src.data.paths import raw_csv_path, validation_report_path
from src.data.schema import BILL_AMT_COLUMNS, PAY_AMT_COLUMNS, PAY_COLUMNS, build_schema

log = get_logger(__name__)

MAX_EXAMPLES = 3
_GROUPED = {"PAY": PAY_COLUMNS, "BILL_AMT": BILL_AMT_COLUMNS, "PAY_AMT": PAY_AMT_COLUMNS}


def _jsonable(value: Any) -> Any:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value if isinstance(value, (int, float, str, bool)) else str(value)


def _schema_errors(df: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        build_schema(params).validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases.copy()
        cases["column"] = cases["column"].where(cases["column"].notna(), None)
        grouped: dict[tuple[str, str], list[Any]] = {}
        for row in cases.itertuples(index=False):
            check = str(row.check)
            if check in ("column_in_schema", "column_in_dataframe"):
                column = str(row.failure_case)  # pandera puts the column name in failure_case
            else:
                column = str(row.column) if row.column is not None else "<dataframe>"
            grouped.setdefault((column, check), []).append(row.failure_case)
        return [
            {
                "column": column,
                "check": check,
                "count": len(values),
                "examples": [_jsonable(v) for v in values[:MAX_EXAMPLES]],
            }
            for (column, check), values in sorted(grouped.items())
        ]
    return []


def _warnings(df: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for group, codes in sorted(params["validation"]["undocumented_codes"].items()):
        for column in _GROUPED.get(group, [group]):
            if column not in df.columns:
                continue
            hits = df[column][df[column].isin(codes)].value_counts().sort_index()
            if not hits.empty:
                warnings.append(
                    {
                        "type": "undocumented_codes",
                        "column": column,
                        "counts": {str(k): int(v) for k, v in hits.items()},
                    }
                )
    id_col = params["data"]["id_column"]
    content_cols = [c for c in df.columns if c != id_col]
    if content_cols:
        duplicated = int(df.duplicated(subset=content_cols).sum())
        if duplicated:
            warnings.append({"type": "duplicate_rows_excluding_id", "count": duplicated})
    return warnings


def validate_dataframe(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Pure validation: returns the report dict (status 'passed' or 'failed')."""
    rules = params["validation"]
    target = params["data"]["target_column"]
    errors = _schema_errors(df, params)

    if len(df) < rules["min_rows"]:
        errors.append(
            {
                "column": "<dataframe>",
                "check": f"min_rows({rules['min_rows']})",
                "count": 1,
                "examples": [len(df)],
            }
        )

    target_rate = None
    if target in df.columns and len(df):
        values = pd.to_numeric(df[target], errors="coerce")
        if values.notna().all() and values.isin([0, 1]).all():
            target_rate = round(float(values.mean()), 6)
            low, high = rules["target_rate_bounds"]
            if not low <= target_rate <= high:
                errors.append(
                    {
                        "column": target,
                        "check": f"target_rate_in({low}, {high})",
                        "count": 1,
                        "examples": [target_rate],
                    }
                )

    return {
        "status": "failed" if errors else "passed",
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "target_rate": target_rate,
        "errors": errors,
        "warnings": _warnings(df, params),
    }


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    csv_path = raw_csv_path(params, smoke)
    report_path = validation_report_path(params, smoke)
    if not csv_path.is_file():
        raise IngestionError(f"raw data not found: {csv_path} (run ingestion first)")

    try:
        df = pd.read_csv(csv_path)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        report = {
            "status": "failed",
            "rows": 0,
            "columns": 0,
            "target_rate": None,
            "errors": [{"column": "<file>", "check": "csv_readable", "count": 1, "examples": []}],
            "warnings": [],
        }
        write_json(report_path, report)
        raise DataValidationError(f"{csv_path.name} is not a readable CSV: {exc}", report) from exc

    report = validate_dataframe(df, params)
    write_json(report_path, report)

    for warning in report["warnings"]:
        log.warning("data warning: %s", warning)
    if report["status"] == "failed":
        summary = ", ".join(
            f"{e['column']}:{e['check']}x{e['count']}" for e in report["errors"][:8]
        )
        raise DataValidationError(
            f"{len(report['errors'])} validation check(s) failed: {summary} (see {report_path})",
            report,
        )
    return {"validation_report": str(report_path), "status": "passed", "rows": report["rows"]}


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Validate data/raw against the strict schema"))
