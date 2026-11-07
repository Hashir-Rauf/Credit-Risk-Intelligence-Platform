"""Load and validate params.yaml (the single source of truth for S1 stages)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from src.common.errors import ConfigError

DEFAULT_PARAMS_FILE = "params.yaml"
VALID_DATA_SOURCES = ("uci", "s3", "local", "synthetic")
REQUIRED_DOMAINS = ("SEX", "EDUCATION", "MARRIAGE")
REQUIRED_RANGES = ("LIMIT_BAL", "AGE", "PAY", "BILL_AMT", "PAY_AMT")

REQUIRED_KEYS = (
    "seed",
    "data.source",
    "data.uci_url",
    "data.s3_uri_env",
    "data.local_path",
    "data.synthetic_rows",
    "data.raw_dir",
    "data.raw_filename",
    "data.ingestion_report",
    "data.processed_dir",
    "data.id_column",
    "data.target_column",
    "data.timestamp_column",
    "smoke.n_rows",
    "smoke.data_dir",
    "validation.report_path",
    "validation.min_rows",
    "validation.target_rate_bounds",
    "validation.domains",
    "validation.ranges",
    "validation.undocumented_codes",
    "split.train",
    "split.val",
    "split.test",
    "features.sensitive_columns",
    "features.exclude_sensitive_from_model",
    "gate.roc_auc_min",
    "mlflow.experiment_name",
    "mlflow.tracking_uri_env",
    "mlflow.registry_model_name",
    "mlflow.challenger_alias",
    "mlflow.champion_alias",
)

_MISSING = object()


def repo_root() -> Path:
    """Repository root (the directory that contains params.yaml and src/)."""
    return Path(__file__).resolve().parents[2]


def resolve_path(path: str | Path) -> Path:
    """Resolve a config path: absolute paths pass through, relative ones are repo-rooted."""
    p = Path(path)
    return p if p.is_absolute() else repo_root() / p


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Read a YAML file that must contain a mapping at the top level."""
    p = resolve_path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {p}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must contain a YAML mapping at the top level")
    return data


def get_param(params: dict[str, Any], dotted: str, default: Any = _MISSING) -> Any:
    """Fetch a nested value with dotted notation, e.g. get_param(p, "split.train")."""
    node: Any = params
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            if default is _MISSING:
                raise ConfigError(f"missing parameter '{dotted}'")
            return default
        node = node[part]
    return node


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_params(params: dict[str, Any]) -> None:
    """Raise ConfigError listing every problem found (not just the first)."""
    errors: list[str] = []

    for key in REQUIRED_KEYS:
        if get_param(params, key, None) is None:
            errors.append(f"missing parameter '{key}'")
    if errors:
        raise ConfigError("; ".join(errors))

    seed = params["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        errors.append("seed must be a non-negative integer")

    source = get_param(params, "data.source")
    if source not in VALID_DATA_SOURCES:
        errors.append(f"data.source must be one of {VALID_DATA_SOURCES}, got {source!r}")

    fractions = [get_param(params, f"split.{k}") for k in ("train", "val", "test")]
    if not all(_is_number(f) and 0 < f < 1 for f in fractions):
        errors.append("split.train/val/test must each be a number in (0, 1)")
    elif not math.isclose(sum(fractions), 1.0, abs_tol=1e-9):
        errors.append(f"split fractions must sum to 1.0, got {sum(fractions):.6f}")

    sensitive = get_param(params, "features.sensitive_columns")
    if not (
        isinstance(sensitive, list)
        and sensitive
        and all(isinstance(c, str) and c for c in sensitive)
    ):
        errors.append("features.sensitive_columns must be a non-empty list of column names")
        sensitive = []
    if not isinstance(get_param(params, "features.exclude_sensitive_from_model"), bool):
        errors.append("features.exclude_sensitive_from_model must be true or false")

    reserved = {get_param(params, "data.id_column"), get_param(params, "data.target_column")}
    overlap = sorted(reserved & set(sensitive))
    if overlap:
        errors.append(f"sensitive_columns must not include id/target columns: {overlap}")

    gate = get_param(params, "gate.roc_auc_min")
    if not (_is_number(gate) and 0.5 < gate < 1.0):
        errors.append("gate.roc_auc_min must be a number in (0.5, 1.0)")

    challenger = get_param(params, "mlflow.challenger_alias")
    champion = get_param(params, "mlflow.champion_alias")
    if challenger == champion:
        errors.append("mlflow.challenger_alias and mlflow.champion_alias must differ")

    _validate_data_and_validation_sections(params, errors)

    if errors:
        raise ConfigError("; ".join(errors))


def _is_pair(value: Any, *, lo_hi_order: bool = True) -> bool:
    ok = isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value)
    return ok and (not lo_hi_order or value[0] <= value[1])


def _validate_data_and_validation_sections(params: dict[str, Any], errors: list[str]) -> None:
    """Checks for the data ingestion / validation settings added in Cycle 1."""
    url = get_param(params, "data.uci_url")
    if not (isinstance(url, str) and url.startswith("https://")):
        errors.append("data.uci_url must be an https:// URL")

    for key in ("data.synthetic_rows", "smoke.n_rows", "validation.min_rows"):
        value = get_param(params, key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{key} must be a positive integer")

    bounds = get_param(params, "validation.target_rate_bounds")
    if not (_is_pair(bounds) and 0 < bounds[0] < bounds[1] < 1):
        errors.append("validation.target_rate_bounds must be [low, high] with 0 < low < high < 1")

    domains = get_param(params, "validation.domains")
    if not isinstance(domains, dict):
        errors.append("validation.domains must be a mapping of column -> allowed values")
    else:
        for col in REQUIRED_DOMAINS:
            allowed = domains.get(col)
            if not (isinstance(allowed, list) and allowed):
                errors.append(f"validation.domains.{col} must be a non-empty list")

    ranges = get_param(params, "validation.ranges")
    if not isinstance(ranges, dict):
        errors.append("validation.ranges must be a mapping of column -> [min, max]")
    else:
        for col in REQUIRED_RANGES:
            if not _is_pair(ranges.get(col)):
                errors.append(f"validation.ranges.{col} must be [min, max] with min <= max")

    undocumented = get_param(params, "validation.undocumented_codes")
    if not (
        isinstance(undocumented, dict) and all(isinstance(v, list) for v in undocumented.values())
    ):
        errors.append("validation.undocumented_codes must be a mapping of column -> list of codes")


def load_params(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the parameters file (defaults to <repo>/params.yaml)."""
    params = load_yaml(path if path is not None else DEFAULT_PARAMS_FILE)
    validate_params(params)
    return params
