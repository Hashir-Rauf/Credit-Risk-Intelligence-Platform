"""Load the scoring model once, plus everything `/model-info` and `/explain` need.

Sources, in order: `MODEL_URI` (an MLflow URI such as `models:/credit-risk-default-model@champion`;
needs mlflow installed and tracking credentials in the environment) or the file at `MODEL_PATH`.

SECURITY: loading a model unpickles it. Only point this at artifacts produced by our own pipeline
(a registry we control or the DVC remote); never at a file a user can upload.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
import yaml

from src.models import inference
from src.serving.settings import DISCLAIMER, Settings

REPORTED_TEST_METRICS = (
    "roc_auc",
    "pr_auc",
    "ks",
    "brier",
    "precision",
    "recall",
    "f1",
    "accuracy",
    "n",
    "positive_rate",
)
RELOAD_COOLDOWN_S = 10.0


class ModelUnavailable(RuntimeError):
    """The model (or its schema) could not be loaded; the API answers 503 until it can."""


@dataclass(frozen=True)
class Bundle:
    model: Any
    schema: dict[str, Any]
    threshold: float
    background: pd.DataFrame | None
    info: dict[str, Any]
    loaded_at: float = field(default_factory=time.time)

    @property
    def inputs(self) -> list[dict[str, Any]]:
        return list(self.schema["inputs"])


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _raw_data_hash(lock_path: Path) -> str | None:
    """md5 of the raw CSV the champion was trained on, as recorded by DVC."""
    try:
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        for out in lock["stages"]["ingest"]["outs"]:
            if str(out["path"]).endswith(".csv"):
                return str(out["md5"])
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        pass
    return None


def _load_model(settings: Settings) -> tuple[Any, str | None]:
    if settings.model_uri:
        try:
            import mlflow.sklearn
        except ImportError as exc:
            raise ModelUnavailable("MODEL_URI is set but mlflow is not installed here") from exc
        try:
            return mlflow.sklearn.load_model(settings.model_uri), None
        except Exception as exc:
            raise ModelUnavailable(
                f"could not load {settings.model_uri}: {type(exc).__name__}"
            ) from exc
    if not settings.model_path.is_file():
        raise ModelUnavailable(f"model file not found: {settings.model_path}")
    try:
        return joblib.load(settings.model_path), _sha256(settings.model_path)
    except Exception as exc:
        raise ModelUnavailable(f"could not read the model file: {type(exc).__name__}") from exc


def _load_schema(settings: Settings) -> dict[str, Any]:
    schema = _read_json(settings.schema_path)
    if not schema or "inputs" not in schema or "decision_threshold" not in schema:
        raise ModelUnavailable(f"feature schema missing or invalid: {settings.schema_path}")
    return schema


def _load_background(settings: Settings) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(settings.background_path)
    except (OSError, ValueError):
        return None


def _single_threaded(estimator: object) -> None:
    """Training uses every core (`n_jobs=-1`); for one-row requests that thread pool is pure
    overhead (measured: 49 ms -> 26 ms median). Batches are capped, so one thread is enough."""
    if hasattr(estimator, "n_jobs"):
        estimator.n_jobs = 1


def warm_up(bundle: Bundle) -> None:
    """Pay one-time costs (SHAP/numba compilation) before the first user request does."""
    record = {}
    for spec in bundle.inputs:
        record[spec["name"]] = spec["allowed"][0] if "allowed" in spec else spec.get("min", 0)
    inference.predict_proba(bundle.model, [record], [s["name"] for s in bundle.inputs])
    if bundle.background is not None:
        from src.models import explain

        explain.explain_records(bundle.model, bundle.background, [record], top_k=1)


def load_bundle(settings: Settings) -> Bundle:
    schema = _load_schema(settings)
    model, model_sha = _load_model(settings)
    metrics = _read_json(settings.metrics_report) or {}
    evaluation = _read_json(settings.evaluation_report) or {}
    test = metrics.get("test", {})
    estimator = model[-1] if hasattr(model, "__getitem__") else model
    _single_threaded(estimator)
    info = {
        "model_name": settings.model_name,
        "model_version": settings.model_version,
        "model_source": settings.model_uri or str(settings.model_path),
        "model_sha256": model_sha,
        "algorithm": type(estimator).__name__,
        "git_sha": settings.git_sha,
        "raw_data_md5": _raw_data_hash(settings.dvc_lock),
        "threshold": float(schema["decision_threshold"]),
        "score": {
            "name": "risk_score",
            "calibrated": False,
            "meaning": "ranking score in [0, 1]; higher means higher modelled risk. "
            "It is not a calibrated probability of default.",
        },
        "test_metrics": {k: test[k] for k in REPORTED_TEST_METRICS if k in test},
        "quality_gate": evaluation.get("gate"),
        "inputs": schema["inputs"],
        "excluded_sensitive": schema.get("excluded_sensitive", []),
        "explanations_available": False,
        "disclaimer": DISCLAIMER,
    }
    background = _load_background(settings)
    info["explanations_available"] = background is not None
    return Bundle(
        model=model,
        schema=schema,
        threshold=float(schema["decision_threshold"]),
        background=background,
        info=info,
    )


class BundleHolder:
    """Holds the bundle; retries loading (at most every few seconds) while it is missing, so a
    model volume that appears after start-up is picked up without restarting the container."""

    def __init__(self, settings: Settings, preload: Bundle | None = None):
        self._settings = settings
        self._bundle = preload
        self._error: str | None = None
        self._last_attempt = 0.0
        self._lock = threading.Lock()
        if preload is None:
            self._try_load()

    def _try_load(self) -> None:
        self._last_attempt = time.monotonic()
        try:
            self._bundle = load_bundle(self._settings)
            self._error = None
        except ModelUnavailable as exc:
            self._error = str(exc)

    def get(self) -> Bundle | None:
        if self._bundle is None and time.monotonic() - self._last_attempt > RELOAD_COOLDOWN_S:
            with self._lock:
                if self._bundle is None:
                    self._try_load()
        return self._bundle

    @property
    def error(self) -> str | None:
        return self._error
