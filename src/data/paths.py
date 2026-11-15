"""Where data stages read and write. Smoke runs are isolated under smoke.data_dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.common.config import resolve_path


def raw_csv_path(params: dict[str, Any], smoke: bool = False) -> Path:
    raw = Path(params["data"]["raw_path"])
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "raw" / raw.name
    return resolve_path(raw)


def _report_path(configured: str, params: dict[str, Any], smoke: bool) -> Path:
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "reports" / Path(configured).name
    return resolve_path(configured)


def provenance_path(params: dict[str, Any], smoke: bool = False) -> Path:
    """Sidecar written only by the synthetic generator; real data has none."""
    csv = raw_csv_path(params, smoke)
    return csv.with_name(csv.name + ".provenance.json")


def validation_report_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_path(params["data"]["validation_report"], params, smoke)


def processed_dir(params: dict[str, Any], smoke: bool = False) -> Path:
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "processed"
    return resolve_path(params["data"]["processed_dir"])


def split_path(params: dict[str, Any], name: str, smoke: bool = False) -> Path:
    return processed_dir(params, smoke) / f"{name}.parquet"


def _smoke_file(configured: str, folder: str, params: dict[str, Any]) -> Path:
    return resolve_path(params["smoke"]["data_dir"]) / folder / Path(configured).name


def feature_schema_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["features"]["schema_path"]
    return _smoke_file(configured, "reports", params) if smoke else resolve_path(configured)


def model_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["train"]["model_path"]
    return _smoke_file(configured, "models", params) if smoke else resolve_path(configured)


def metrics_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["train"]["metrics_path"]
    return _smoke_file(configured, "reports", params) if smoke else resolve_path(configured)


def last_run_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["train"]["last_run_path"]
    return _smoke_file(configured, "reports", params) if smoke else resolve_path(configured)


def evaluation_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["evaluate"]["evaluation_path"]
    return _smoke_file(configured, "reports", params) if smoke else resolve_path(configured)


def plots_dir(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["train"]["plots_dir"]
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "reports" / Path(configured).name
    return resolve_path(configured)
