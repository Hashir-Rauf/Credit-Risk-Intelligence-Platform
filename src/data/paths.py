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


def _report_file(section: str, key: str, params: dict[str, Any], smoke: bool) -> Path:
    configured = params[section][key]
    return _smoke_file(configured, "reports", params) if smoke else resolve_path(configured)


def tuning_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("tune", "results_path", params, smoke)


def comparison_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("tune", "comparison_path", params, smoke)


def promotion_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("promotion", "decision_path", params, smoke)


def registration_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("promotion", "registration_path", params, smoke)


def feast_dir(params: dict[str, Any], smoke: bool = False) -> Path:
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "feast"
    return resolve_path(params["feast"]["data_dir"])


def feast_source_dir(params: dict[str, Any], smoke: bool = False) -> Path:
    return feast_dir(params, smoke) / "source"


def feast_source_path(params: dict[str, Any], view: str, smoke: bool = False) -> Path:
    return feast_source_dir(params, smoke) / f"{view}.parquet"


def feast_registry_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return feast_dir(params, smoke) / "registry.db"


def feast_online_store_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return feast_dir(params, smoke) / "online_store.db"


def feast_report_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("feast", "report_path", params, smoke)


def fairness_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("fairness", "report_path", params, smoke)


def explanations_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("explain", "report_path", params, smoke)


def explainer_background_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["explain"]["background_path"]
    return _smoke_file(configured, "models", params) if smoke else resolve_path(configured)


def drift_report_path(params: dict[str, Any], smoke: bool = False) -> Path:
    return _report_file("simulate", "report_path", params, smoke)


def reference_path(params: dict[str, Any], smoke: bool = False) -> Path:
    configured = params["simulate"]["reference_path"]
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "reference" / Path(configured).name
    return resolve_path(configured)


def simulated_batches_dir(params: dict[str, Any], smoke: bool = False) -> Path:
    if smoke:
        return resolve_path(params["smoke"]["data_dir"]) / "simulated"
    return resolve_path(params["simulate"]["batches_dir"])
