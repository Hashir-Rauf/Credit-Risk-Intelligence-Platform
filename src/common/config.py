"""Load and validate params.yaml (the single source of truth for S1 stages)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from src.common.errors import ConfigError

DEFAULT_PARAMS_FILE = "params.yaml"
MODEL_FAMILIES = ("logistic_regression", "random_forest", "hist_gradient_boosting")
VALID_ALGORITHMS = (*MODEL_FAMILIES, "tuned")
REQUIRED_DOMAINS = ("SEX", "EDUCATION", "MARRIAGE")
REQUIRED_RANGES = ("LIMIT_BAL", "AGE", "PAY", "BILL_AMT", "PAY_AMT")

REQUIRED_KEYS = (
    "seed",
    "data.source_url",
    "data.raw_path",
    "data.validation_report",
    "data.processed_dir",
    "data.id_column",
    "data.target_column",
    "data.timestamp_column",
    "smoke.n_rows",
    "smoke.data_dir",
    "validation.domains",
    "validation.ranges",
    "split.train",
    "split.val",
    "split.test",
    "features.sensitive_columns",
    "features.exclude_sensitive_from_model",
    "features.schema_path",
    "features.use_feast_for_training",
    "feast.project",
    "feast.repo_dir",
    "feast.data_dir",
    "feast.entity_join_key",
    "feast.simulated_start",
    "feast.simulated_days",
    "feast.label_lag_days",
    "feast.ttl_days",
    "feast.max_null_fraction",
    "feast.parity_sample",
    "feast.report_path",
    "train.algorithm",
    "train.model_path",
    "train.metrics_path",
    "train.plots_dir",
    "train.last_run_path",
    "evaluate.decision_threshold",
    "evaluate.evaluation_path",
    "evaluate.repro_tolerance",
    "gate.roc_auc_min",
    "tune.families",
    "tune.n_trials",
    "tune.smoke_n_trials",
    "tune.simplicity_margin",
    "tune.results_path",
    "tune.comparison_path",
    "promotion.min_auc_gain",
    "promotion.max_regression_drop",
    "promotion.fairness_check",
    "promotion.max_fairness_regression",
    "fairness.attributes",
    "fairness.age_band_edges",
    "fairness.min_group_size",
    "fairness.min_disparate_impact",
    "fairness.max_tpr_gap",
    "fairness.max_auc_gap",
    "fairness.enforce",
    "fairness.run_ablation",
    "fairness.ablation_min_auc_gain",
    "fairness.report_path",
    "explain.background_size",
    "explain.importance_sample",
    "explain.top_k",
    "explain.report_path",
    "explain.background_path",
    "simulate.batch_size",
    "simulate.reference_path",
    "simulate.batches_dir",
    "simulate.report_path",
    "simulate.psi_bins",
    "simulate.psi_threshold",
    "simulate.drift_share_alert",
    "simulate.scenarios",
    "promotion.decision_path",
    "promotion.registration_path",
    "mlflow.experiment_name",
    "mlflow.tracking_uri_env",
    "mlflow.default_tracking_uri",
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
    _validate_training_sections(params, errors)

    if errors:
        raise ConfigError("; ".join(errors))


def _is_pair(value: Any, *, lo_hi_order: bool = True) -> bool:
    ok = isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value)
    return ok and (not lo_hi_order or value[0] <= value[1])


def _validate_data_and_validation_sections(params: dict[str, Any], errors: list[str]) -> None:
    """Checks for the data ingestion / validation settings added in Cycle 1."""
    url = get_param(params, "data.source_url")
    if not (isinstance(url, str) and url.startswith("https://")):
        errors.append("data.source_url must be an https:// URL")

    for key in ("smoke.n_rows",):
        value = get_param(params, key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{key} must be a positive integer")

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


def _validate_training_sections(params: dict[str, Any], errors: list[str]) -> None:
    """Checks for the training / evaluation settings added in Cycle 2."""
    algorithm = get_param(params, "train.algorithm")
    if algorithm not in VALID_ALGORITHMS:
        errors.append(f"train.algorithm must be one of {VALID_ALGORITHMS}, got {algorithm!r}")

    threshold = get_param(params, "evaluate.decision_threshold")
    if not (_is_number(threshold) and 0 < threshold < 1):
        errors.append("evaluate.decision_threshold must be a number in (0, 1)")

    tolerance = get_param(params, "evaluate.repro_tolerance")
    if not (_is_number(tolerance) and 0 <= tolerance < 1e-3):
        errors.append("evaluate.repro_tolerance must be a number in [0, 0.001)")

    if not str(get_param(params, "mlflow.default_tracking_uri")).strip():
        errors.append("mlflow.default_tracking_uri must not be empty")

    _validate_feast_section(params, errors)

    families = get_param(params, "tune.families")
    if not (isinstance(families, list) and families and set(families) <= set(MODEL_FAMILIES)):
        errors.append(f"tune.families must be a non-empty list drawn from {MODEL_FAMILIES}")
    for key in ("tune.n_trials", "tune.smoke_n_trials"):
        value = get_param(params, key)
        if not (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
            errors.append(f"{key} must be an integer >= 1")
    folds = get_param(params, "tune.cv_folds", None)
    if folds is not None and not (
        isinstance(folds, int) and not isinstance(folds, bool) and (folds == 0 or 2 <= folds <= 10)
    ):
        errors.append("tune.cv_folds must be 0 (off) or an integer from 2 to 10")
    margin = get_param(params, "tune.simplicity_margin")
    if not (_is_number(margin) and 0 <= margin < 0.05):
        errors.append("tune.simplicity_margin must be a number in [0, 0.05)")
    gain = get_param(params, "promotion.min_auc_gain")
    if not (_is_number(gain) and 0 <= gain < 0.5):
        errors.append("promotion.min_auc_gain must be a number in [0, 0.5)")
    drop = get_param(params, "promotion.max_regression_drop")
    if not (_is_number(drop) and 0 <= drop < 0.5):
        errors.append("promotion.max_regression_drop must be a number in [0, 0.5)")
    if not isinstance(get_param(params, "promotion.fairness_check"), bool):
        errors.append("promotion.fairness_check must be true or false")
    tolerance = get_param(params, "promotion.max_fairness_regression")
    if not (_is_number(tolerance) and 0 <= tolerance < 0.5):
        errors.append("promotion.max_fairness_regression must be a number in [0, 0.5)")
    _validate_cycle5_sections(params, errors)


def _validate_feast_section(params: dict[str, Any], errors: list[str]) -> None:
    """Checks for the feature-store settings added in Cycle 4."""
    from datetime import date

    use_feast = get_param(params, "features.use_feast_for_training")
    if not isinstance(use_feast, bool):
        errors.append("features.use_feast_for_training must be true or false")
    elif use_feast and not get_param(params, "features.exclude_sensitive_from_model"):
        errors.append(
            "features.use_feast_for_training needs features.exclude_sensitive_from_model: true "
            "(sensitive attributes are not stored in the feature store)"
        )

    start = get_param(params, "feast.simulated_start")
    try:
        date.fromisoformat(str(start))
    except ValueError:
        errors.append(f"feast.simulated_start must be an ISO date (YYYY-MM-DD), got {start!r}")
    for key, minimum in (("feast.simulated_days", 1), ("feast.parity_sample", 1)):
        value = get_param(params, key)
        if not (isinstance(value, int) and not isinstance(value, bool) and value >= minimum):
            errors.append(f"{key} must be an integer >= {minimum}")
    lag = get_param(params, "feast.label_lag_days")
    ttl = get_param(params, "feast.ttl_days")
    if not (isinstance(lag, int) and not isinstance(lag, bool) and lag >= 0):
        errors.append("feast.label_lag_days must be an integer >= 0")
    if not (isinstance(ttl, int) and not isinstance(ttl, bool) and ttl >= 1):
        errors.append("feast.ttl_days must be an integer >= 1")
    elif isinstance(lag, int) and ttl < lag:
        errors.append("feast.ttl_days must be >= feast.label_lag_days")
    nulls = get_param(params, "feast.max_null_fraction")
    if not (_is_number(nulls) and 0 <= nulls <= 1):
        errors.append("feast.max_null_fraction must be a number in [0, 1]")
    if not str(get_param(params, "feast.entity_join_key")).strip():
        errors.append("feast.entity_join_key must not be empty")


FAIRNESS_ATTRIBUTES = ("SEX", "AGE_BAND", "MARRIAGE", "EDUCATION")
SCENARIO_OPS = ("multiply", "add")


def _positive_int(value: Any, minimum: int = 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _validate_cycle5_sections(params: dict[str, Any], errors: list[str]) -> None:
    """Checks for the fairness, explanation and drift-simulation settings added in Cycle 5."""
    attributes = get_param(params, "fairness.attributes")
    if not (
        isinstance(attributes, list) and attributes and set(attributes) <= set(FAIRNESS_ATTRIBUTES)
    ):
        errors.append(
            f"fairness.attributes must be a non-empty list drawn from {FAIRNESS_ATTRIBUTES}"
        )
    edges = get_param(params, "fairness.age_band_edges")
    if not (
        isinstance(edges, list)
        and edges
        and all(_is_number(e) for e in edges)
        and edges == sorted(set(edges))
    ):
        errors.append("fairness.age_band_edges must be a strictly increasing list of numbers")
    if not _positive_int(get_param(params, "fairness.min_group_size")):
        errors.append("fairness.min_group_size must be an integer >= 1")
    ratio = get_param(params, "fairness.min_disparate_impact")
    if not (_is_number(ratio) and 0 < ratio <= 1):
        errors.append("fairness.min_disparate_impact must be a number in (0, 1]")
    for key in ("fairness.max_tpr_gap", "fairness.max_auc_gap", "fairness.ablation_min_auc_gain"):
        value = get_param(params, key)
        if not (_is_number(value) and 0 <= value <= 1):
            errors.append(f"{key} must be a number in [0, 1]")
    for key in ("fairness.enforce", "fairness.run_ablation"):
        if not isinstance(get_param(params, key), bool):
            errors.append(f"{key} must be true or false")

    for key in ("explain.background_size", "explain.importance_sample", "explain.top_k"):
        if not _positive_int(get_param(params, key)):
            errors.append(f"{key} must be an integer >= 1")

    for key in ("simulate.batch_size", "simulate.psi_bins"):
        if not _positive_int(get_param(params, key), 2 if key.endswith("bins") else 1):
            errors.append(f"{key} must be an integer >= {2 if key.endswith('bins') else 1}")
    threshold = get_param(params, "simulate.psi_threshold")
    if not (_is_number(threshold) and threshold > 0):
        errors.append("simulate.psi_threshold must be a positive number")
    share = get_param(params, "simulate.drift_share_alert")
    if not (_is_number(share) and 0 < share <= 1):
        errors.append("simulate.drift_share_alert must be a number in (0, 1]")
    scenarios = get_param(params, "simulate.scenarios")
    if not (isinstance(scenarios, list) and scenarios):
        errors.append("simulate.scenarios must be a non-empty list")
        return
    names = [s.get("name") if isinstance(s, dict) else None for s in scenarios]
    if any(not n for n in names) or len(set(names)) != len(names):
        errors.append("simulate.scenarios need unique, non-empty names")
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("expect_alert"), bool):
            errors.append("every simulate scenario needs a boolean expect_alert")
            continue
        for change in scenario.get("changes") or []:
            if not (
                isinstance(change, dict)
                and change.get("op") in SCENARIO_OPS
                and _is_number(change.get("value"))
                and isinstance(change.get("columns"), list)
                and change["columns"]
            ):
                errors.append(
                    f"scenario {scenario.get('name')!r}: each change needs columns, "
                    f"op ({SCENARIO_OPS}) and a number"
                )


def load_params(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the parameters file (defaults to <repo>/params.yaml)."""
    params = load_yaml(path if path is not None else DEFAULT_PARAMS_FILE)
    validate_params(params)
    return params
