"""Metrics, plots, the quality gate and the `evaluate` stage.

    python -m src.models.evaluate [--params params.yaml] [--smoke]

The stage reloads the trained model, recomputes test metrics, checks they reproduce what
`train` recorded, then applies the gate. A model below `gate.roc_auc_min` exits with code 3
and must not proceed to deployment (rules.md §5). The threshold lives in params.yaml and is
never lowered to make a run pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from src.common.cli import stage_main
from src.common.errors import GateFailedError, IngestionError, PipelineError
from src.common.io import read_json, write_json
from src.common.logging_utils import get_logger
from src.data.paths import evaluation_path, last_run_path, metrics_path, model_path, split_path
from src.features.feast_features import attach_feast_features
from src.features.preprocessing import model_input_columns
from src.models.tracking import tracking_uri

log = get_logger(__name__)

REPRODUCED_METRICS = ("roc_auc", "pr_auc", "ks", "brier", "log_loss")


def compute_metrics(y_true: Any, y_prob: Any, threshold: float) -> dict[str, float]:
    """Ranking metrics (ROC-AUC, PR-AUC, KS), calibration (Brier, log-loss) and threshold
    metrics (precision, recall, F1, accuracy, confusion counts)."""
    y = np.asarray(y_true).astype(int)
    p = np.asarray(y_prob, dtype=float)
    if y.shape != p.shape or y.size == 0:
        raise ValueError("y_true and y_prob must be non-empty and the same length")
    if len(np.unique(y)) < 2:
        raise ValueError("metrics need both classes present in y_true")
    if np.isnan(p).any() or (p < 0).any() or (p > 1).any():
        raise ValueError("y_prob must be probabilities in [0, 1] without NaN")

    fpr, tpr, _ = roc_curve(y, p)
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = (int(v) for v in confusion_matrix(y, pred, labels=[0, 1]).ravel())
    precision = float(precision_score(y, pred, zero_division=0))
    recall = float(recall_score(y, pred, zero_division=0))
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "ks": float(np.max(tpr - fpr)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "precision": precision,
        "recall": recall,
        "f1": float(f1),
        "accuracy": float((pred == y).mean()),
        "threshold": float(threshold),
        "n": int(y.size),
        "positive_rate": float(y.mean()),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def check_gate(metrics: dict[str, float], params: dict[str, Any]) -> dict[str, Any]:
    """Pure gate decision on the test ROC-AUC. Returns {metric, value, threshold, passed}."""
    threshold = float(params["gate"]["roc_auc_min"])
    value = float(metrics["roc_auc"])
    return {
        "metric": "roc_auc",
        "value": value,
        "threshold": threshold,
        "passed": value >= threshold,
    }


REGRESSION_METRICS = ("roc_auc", "pr_auc", "ks")


def check_regression(
    candidate: dict[str, float], baseline: dict[str, float], max_drop: float
) -> list[str]:
    """Names of the ranking metrics where `candidate` is worse than `baseline` by more than
    `max_drop` (an empty list means no regression)."""
    return [m for m in REGRESSION_METRICS if baseline[m] - candidate[m] > max_drop + 1e-12]


def save_plots(
    y_true: Any, y_prob: Any, threshold: float, out_dir: Path, split: str = "test"
) -> list[Path]:
    """ROC, precision-recall, calibration and confusion-matrix PNGs (headless, no pyplot)."""
    from matplotlib.figure import Figure

    y = np.asarray(y_true).astype(int)
    p = np.asarray(y_prob, dtype=float)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def finish(fig: Figure, name: str) -> None:
        path = out_dir / f"{name}_{split}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=100)
        paths.append(path)

    fpr, tpr, _ = roc_curve(y, p)
    fig = Figure(figsize=(5, 4))
    ax = fig.subplots()
    ax.plot(fpr, tpr, label=f"AUC = {roc_auc_score(y, p):.3f}")
    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8)
    ax.set(xlabel="False positive rate", ylabel="True positive rate", title=f"ROC ({split})")
    ax.legend(loc="lower right")
    finish(fig, "roc_curve")

    precision, recall, _ = precision_recall_curve(y, p)
    fig = Figure(figsize=(5, 4))
    ax = fig.subplots()
    ax.plot(recall, precision, label=f"AP = {average_precision_score(y, p):.3f}")
    ax.axhline(y.mean(), color="k", linestyle="--", linewidth=0.8, label="base rate")
    ax.set(xlabel="Recall", ylabel="Precision", title=f"Precision-recall ({split})")
    ax.legend(loc="upper right")
    finish(fig, "pr_curve")

    frac_pos, mean_pred = calibration_curve(y, p, n_bins=10, strategy="quantile")
    fig = Figure(figsize=(5, 4))
    ax = fig.subplots()
    ax.plot(mean_pred, frac_pos, "o-", label="model")
    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8, label="perfect")
    ax.set(xlabel="Mean predicted probability", ylabel="Observed default rate")
    ax.set_title(f"Calibration ({split})")
    ax.legend(loc="upper left")
    finish(fig, "calibration")

    matrix = confusion_matrix(y, (p >= threshold).astype(int), labels=[0, 1])
    fig = Figure(figsize=(4.5, 4))
    ax = fig.subplots()
    ax.imshow(matrix, cmap="Blues")
    for (i, j), value in np.ndenumerate(matrix):
        ax.text(j, i, str(value), ha="center", va="center")
    ax.set(xticks=[0, 1], yticks=[0, 1], xlabel="Predicted", ylabel="Actual")
    ax.set_title(f"Confusion matrix ({split}, threshold {threshold:g})")
    finish(fig, "confusion_matrix")
    return paths


def _tag_mlflow_run(params: dict[str, Any], smoke: bool, gate: dict[str, Any]) -> None:
    """Best-effort: record the gate outcome on the training run. Never fails the stage."""
    path = last_run_path(params, smoke)
    if not path.is_file():
        return
    try:
        from mlflow.tracking import MlflowClient

        run_id = read_json(path)["run_id"]
        client = MlflowClient(tracking_uri=tracking_uri(params, smoke))
        client.set_tag(run_id, "gate_passed", str(gate["passed"]).lower())
        client.set_tag(run_id, "gate_roc_auc_min", str(gate["threshold"]))
    except Exception as exc:
        log.warning("could not tag MLflow run with the gate result: %s", exc)


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    model_file, test_file = model_path(params, smoke), split_path(params, "test", smoke)
    for needed in (model_file, test_file, metrics_path(params, smoke)):
        if not needed.is_file():
            raise IngestionError(f"{needed} not found (run the earlier stages first)")

    model = joblib.load(model_file)  # trusted, locally produced artifact only
    test = attach_feast_features({"test": pd.read_parquet(test_file)}, params, smoke)["test"]
    prob = model.predict_proba(test[model_input_columns(params)])[:, 1]
    threshold = params["evaluate"]["decision_threshold"]
    metrics = compute_metrics(test[params["data"]["target_column"]], prob, threshold)

    recorded = read_json(metrics_path(params, smoke))["test"]
    deviation = max(abs(metrics[k] - recorded[k]) for k in REPRODUCED_METRICS)
    if deviation > params["evaluate"]["repro_tolerance"]:
        raise PipelineError(
            f"evaluation does not reproduce training metrics (max deviation {deviation:.3g}); "
            "model, data or code changed since `train`"
        )

    gate = check_gate(metrics, params)
    write_json(evaluation_path(params, smoke), {"gate": gate, "test_metrics": metrics})
    _tag_mlflow_run(params, smoke, gate)
    log.info(
        "gate: test %s = %.4f (min %.4f) -> %s",
        gate["metric"],
        gate["value"],
        gate["threshold"],
        "PASSED" if gate["passed"] else "FAILED",
    )
    if not gate["passed"]:
        raise GateFailedError(
            f"test {gate['metric']} {gate['value']:.4f} is below the minimum "
            f"{gate['threshold']:.4f}"
        )
    return {"gate": "passed", "roc_auc": round(gate["value"], 4)}


if __name__ == "__main__":
    raise SystemExit(
        stage_main(run, description="Re-evaluate the model on the test split and apply the gate")
    )
