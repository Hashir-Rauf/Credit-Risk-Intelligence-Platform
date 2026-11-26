"""Runtime configuration for the API, read from environment variables only.

Nothing secret has a default: the API key must be supplied, or anonymous access must be switched on
explicitly (`API_ALLOW_ANONYMOUS=1`, local development only). The service refuses to start otherwise
(fail closed).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DISCLAIMER = (
    "Educational model for a university course. Not a real lending decision tool. "
    "risk_score is a model ranking score, not a calibrated probability of default."
)


class SettingsError(RuntimeError):
    """The environment does not describe a safe, usable configuration."""


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from exc
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    allow_anonymous: bool
    model_path: Path
    background_path: Path
    schema_path: Path
    metrics_report: Path
    evaluation_report: Path
    dvc_lock: Path
    model_uri: str | None
    model_name: str
    model_version: str
    git_sha: str
    max_batch: int
    max_body_bytes: int
    explain_max_records: int
    warmup: bool
    log_level: str
    prediction_log_path: Path | None = None
    prediction_log_retention_days: int = 30
    prediction_log_salt: str = ""
    prediction_log_s3_uri: str | None = None
    prediction_log_s3_interval_s: int = 300
    rate_limit_per_minute: int = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        api_key = env.get("API_KEY") or None
        allow_anonymous = env.get("API_ALLOW_ANONYMOUS", "") == "1"
        if api_key is None and not allow_anonymous:
            raise SettingsError(
                "API_KEY is not set. Set it, or set API_ALLOW_ANONYMOUS=1 "
                "for local development only."
            )
        if api_key is not None and len(api_key) < 16 and not allow_anonymous:
            raise SettingsError("API_KEY must be at least 16 characters")
        return cls(
            api_key=api_key,
            allow_anonymous=allow_anonymous,
            model_path=Path(env.get("MODEL_PATH", "models/model.joblib")),
            background_path=Path(
                env.get("EXPLAINER_BACKGROUND_PATH", "models/explainer_background.parquet")
            ),
            schema_path=Path(env.get("FEATURE_SCHEMA_PATH", "reports/feature_schema.json")),
            metrics_report=Path(env.get("METRICS_REPORT_PATH", "reports/metrics.json")),
            evaluation_report=Path(env.get("EVALUATION_REPORT_PATH", "reports/evaluation.json")),
            dvc_lock=Path(env.get("DVC_LOCK_PATH", "dvc.lock")),
            model_uri=env.get("MODEL_URI") or None,
            model_name=env.get("MODEL_NAME", "credit-risk-default-model"),
            model_version=env.get("MODEL_VERSION", "unknown"),
            git_sha=env.get("GIT_SHA", "unknown"),
            max_batch=_int(env, "MAX_BATCH", 100, 1, 10_000),
            max_body_bytes=_int(env, "MAX_BODY_BYTES", 1_000_000, 1_024, 50_000_000),
            explain_max_records=_int(env, "EXPLAIN_MAX_RECORDS", 5, 1, 100),
            warmup=env.get("WARMUP_EXPLAIN", "1") != "0",
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            prediction_log_path=(
                Path(env["PREDICTION_LOG_PATH"]) if env.get("PREDICTION_LOG_PATH") else None
            ),
            prediction_log_retention_days=_int(env, "PREDICTION_LOG_RETENTION_DAYS", 30, 1, 365),
            prediction_log_salt=env.get("PREDICTION_LOG_SALT", ""),
            prediction_log_s3_uri=env.get("PREDICTION_LOG_S3_URI") or None,
            prediction_log_s3_interval_s=_int(env, "PREDICTION_LOG_S3_INTERVAL_S", 300, 30, 86_400),
            rate_limit_per_minute=_int(env, "RATE_LIMIT_PER_MINUTE", 0, 0, 1_000_000),
        )
