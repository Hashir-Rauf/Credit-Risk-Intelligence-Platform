"""Fit the model on the train split, evaluate on val/test, log everything to MLflow.

    python -m src.models.train [--params params.yaml] [--smoke]

`train` always completes and records its run, even when the model is weak: a rejected
candidate is evidence too. The gate is applied by the separate `evaluate` stage (exit 3).
Hyperparameters come from configs/model_params.yaml; every random seed derives from
params.yaml `seed`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from src.common.cli import stage_main
from src.common.config import load_yaml, repo_root
from src.common.errors import ConfigError, IngestionError
from src.common.io import write_json
from src.common.logging_utils import get_logger
from src.common.seed import derive_seed
from src.data.paths import (
    feature_schema_path,
    last_run_path,
    metrics_path,
    model_path,
    plots_dir,
    split_path,
)
from src.features.preprocessing import SPLITS, build_pipeline, model_input_columns
from src.models.evaluate import compute_metrics, save_plots
from src.models.tracking import artifact_location, experiment_name, run_tags, tracking_uri

log = get_logger(__name__)

MODEL_PARAMS_FILE = "configs/model_params.yaml"
TRUSTED_TYPES = ["src.features.preprocessing.engineer_features"]
# Tree node storage is produced by our own training run, so trusting it is safe for tree models.
TREE_TYPES = ["sklearn.tree._tree.Tree"]


def build_estimator(algorithm: str, hyperparameters: dict[str, Any], seed: int) -> BaseEstimator:
    """Estimator for `algorithm`, seeded from params.yaml."""
    random_state = derive_seed(seed, f"model:{algorithm}")
    if algorithm == "logistic_regression":
        return LogisticRegression(**hyperparameters, random_state=random_state)
    if algorithm == "random_forest":
        return RandomForestClassifier(**hyperparameters, random_state=random_state)
    raise ConfigError(f"unknown algorithm {algorithm!r}")


def load_splits(params: dict[str, Any], smoke: bool) -> dict[str, pd.DataFrame]:
    frames = {}
    for name in SPLITS:
        path = split_path(params, name, smoke)
        if not path.is_file():
            raise IngestionError(f"{path} not found (run the preprocess stage first)")
        frames[name] = pd.read_parquet(path)
    return frames


def _save_model(pipeline: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    joblib.dump(pipeline, tmp, compress=3)
    os.replace(tmp, path)


def _log_to_mlflow(
    params: dict[str, Any],
    smoke: bool,
    algorithm: str,
    hyperparameters: dict[str, Any],
    pipeline: Any,
    summary: dict[str, Any],
    sample: pd.DataFrame,
    duration_s: float,
) -> tuple[str, str]:
    import mlflow
    from mlflow.models import infer_signature

    uri = tracking_uri(params, smoke)
    mlflow.set_tracking_uri(uri)
    name = experiment_name(params, smoke)
    location = artifact_location(uri)
    if location and mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(name, artifact_location=location)
    mlflow.set_experiment(name)
    with mlflow.start_run(run_name=f"{algorithm}-seed{params['seed']}") as active:
        mlflow.set_tags(run_tags(params, smoke, algorithm))
        mlflow.log_params(
            {
                "algorithm": algorithm,
                **{f"hp_{k}": v for k, v in hyperparameters.items()},
                "seed": params["seed"],
                "decision_threshold": params["evaluate"]["decision_threshold"],
                "exclude_sensitive": params["features"]["exclude_sensitive_from_model"],
                "n_model_features": summary["n_model_features"],
                **{f"n_{k}": v for k, v in summary["sizes"].items()},
            }
        )
        for split in ("val", "test"):
            mlflow.log_metrics({f"{split}_{k}": v for k, v in summary[split].items()})
        mlflow.log_metric("train_duration_s", duration_s)

        mlflow.log_artifacts(str(plots_dir(params, smoke)), artifact_path="plots")
        for artifact in (metrics_path(params, smoke), feature_schema_path(params, smoke)):
            mlflow.log_artifact(str(artifact), artifact_path="reports")
        mlflow.log_artifact(str(repo_root() / "params.yaml"), artifact_path="config")
        mlflow.log_artifact(str(repo_root() / MODEL_PARAMS_FILE), artifact_path="config")

        # Floats in the signature accept 2 and 2.0 alike (JSON clients often send either).
        sample = sample.astype("float64")
        signature = infer_signature(sample, pipeline.predict_proba(sample)[:, 1])
        model_info = mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=signature,
            input_example=sample.head(3),
            code_paths=[str(repo_root() / "src")],  # makes the model artifact self-contained
            # MLflow >= 3 serialises with skops, which refuses unknown types. Trust exactly the
            # one function our pipeline uses; anything else stays blocked.
            skops_trusted_types=TRUSTED_TYPES
            + (TREE_TYPES if algorithm == "random_forest" else []),
        )
        return active.info.run_id, model_info.model_uri


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    algorithm = params["train"]["algorithm"]
    hyperparameters = dict(load_yaml(MODEL_PARAMS_FILE).get(algorithm) or {})
    target = params["data"]["target_column"]
    columns = model_input_columns(params)

    splits = load_splits(params, smoke)
    train, val, test = (splits[name] for name in SPLITS)
    pipeline = build_pipeline(params, build_estimator(algorithm, hyperparameters, params["seed"]))

    started = time.perf_counter()
    pipeline.fit(train[columns], train[target])
    duration_s = time.perf_counter() - started

    threshold = params["evaluate"]["decision_threshold"]
    probabilities = {
        name: pipeline.predict_proba(frame[columns])[:, 1]
        for name, frame in (("val", val), ("test", test))
    }
    summary: dict[str, Any] = {
        "algorithm": algorithm,
        "seed": params["seed"],
        "smoke": smoke,
        "threshold": threshold,
        "sizes": {
            name: int(len(frame)) for name, frame in zip(SPLITS, (train, val, test), strict=True)
        },
        "n_model_features": int(len(pipeline.named_steps["prep"].get_feature_names_out())),
        "val": compute_metrics(val[target], probabilities["val"], threshold),
        "test": compute_metrics(test[target], probabilities["test"], threshold),
    }

    _save_model(pipeline, model_path(params, smoke))
    write_json(metrics_path(params, smoke), summary)  # deterministic: no timings or run ids
    save_plots(test[target], probabilities["test"], threshold, plots_dir(params, smoke), "test")

    run_id, model_uri = _log_to_mlflow(
        params,
        smoke,
        algorithm,
        hyperparameters,
        pipeline,
        summary,
        test[columns].head(50),
        duration_s,
    )
    write_json(last_run_path(params, smoke), {"run_id": run_id, "model_uri": model_uri})
    log.info(
        "trained %s: val AUC %.4f, test AUC %.4f (%.1fs), MLflow run %s",
        algorithm,
        summary["val"]["roc_auc"],
        summary["test"]["roc_auc"],
        duration_s,
        run_id,
    )
    return {
        "model": str(model_path(params, smoke)),
        "test_roc_auc": round(summary["test"]["roc_auc"], 4),
    }


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Train the model and log the run to MLflow"))
