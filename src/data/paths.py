"""Where data stages read and write. Smoke runs are isolated under smoke.data_dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.common.config import resolve_path


def raw_csv_path(params: dict[str, Any], smoke: bool = False) -> Path:
    if smoke:
        base = resolve_path(params["smoke"]["data_dir"]) / "raw"
    else:
        base = resolve_path(params["data"]["raw_dir"])
    return base / params["data"]["raw_filename"]


def _report_path(configured: str, params: dict[str, Any], smoke: bool) -> Path:
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "reports" / Path(configured).name
    return resolve_path(configured)


def ingestion_report_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_path(params["data"]["ingestion_report"], params, smoke)


def validation_report_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_path(params["validation"]["report_path"], params, smoke)
