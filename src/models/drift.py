"""Reference data and controlled, SIMULATED drift scenarios for the monitoring demo.

    python -m src.models.drift [--params params.yaml] [--smoke]

Outputs (consumed by the Evidently job / retrain DAG, rules.md section 9):
  * `data/reference/reference.parquet`: the training split's model inputs + the model's score
    + the label. This is what "no drift" means for the deployed model.
  * `data/simulated/<scenario>.parquet`: batches of `simulate.batch_size` test rows with the
    shifts configured in params.yaml `simulate.scenarios` (inflated limits, payment delays...).
    Shifted values are clipped to the validation ranges, so batches stay valid data: they test
    drift detection, not the validation stage. Labels are the real ones (UCI has ground truth),
    so the batches also stand in for delayed labels. EVERYTHING here is simulated.
  * `reports/drift_scenarios.json`: per scenario, PSI and KS per feature, the share of drifted
    features, whether the alert fires, whether that matches the scenario's expectation, and
    the model's AUC on the batch (the effect of drift on performance).

The detector is a lightweight PSI/KS implementation ("Evidently or an equivalent approach");
`psi`, `ks_statistic` and `drift_report` are plain functions the monitoring job can reuse.
"""

from __future__ import annotations

from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp
from sklearn.metrics import roc_auc_score

from src.common.cli import stage_main
from src.common.errors import ConfigError, IngestionError, PipelineError
from src.common.io import write_json, write_parquet
from src.common.logging_utils import get_logger
from src.common.seed import make_rng
from src.data.paths import (
    drift_report_path,
    metrics_path,
    model_path,
    reference_path,
    simulated_batches_dir,
    split_path,
)
from src.features.feast_features import attach_feast_features
from src.features.preprocessing import RAW_INPUT_COLUMNS, model_input_columns

log = get_logger(__name__)

EPSILON = 1e-4  # floor for empty bins so PSI stays finite
SCORE_COLUMN = "score"
RANGE_KEYS = {"LIMIT_BAL": "LIMIT_BAL", "PAY": "PAY", "BILL_AMT": "BILL_AMT", "PAY_AMT": "PAY_AMT"}


# --------------------------------------------------------------------------- drift metrics


def _bucket_counts(
    reference: np.ndarray, current: np.ndarray, bins: int
) -> tuple[np.ndarray, np.ndarray]:
    """Counts per bucket. Few distinct values (status codes): one bucket per value plus an
    'unseen' bucket. Otherwise reference quantile bins with open ends."""
    values = np.unique(reference)
    if len(values) <= bins:
        ref = np.array([(reference == v).sum() for v in values] + [0])
        cur = np.array([(current == v).sum() for v in values] + [(~np.isin(current, values)).sum()])
        return ref, cur
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    return np.histogram(reference, edges)[0], np.histogram(current, edges)[0]


def psi(reference: Any, current: Any, bins: int = 10) -> float:
    """Population Stability Index of `current` against `reference`.
    < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 major shift (common rule of thumb)."""
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    if reference.size == 0 or current.size == 0:
        raise ValueError("reference and current must be non-empty")
    ref_counts, cur_counts = _bucket_counts(reference, current, bins)
    ref_share = np.maximum(ref_counts / ref_counts.sum(), EPSILON)
    cur_share = np.maximum(cur_counts / cur_counts.sum(), EPSILON)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def ks_statistic(reference: Any, current: Any) -> float:
    """Two-sample Kolmogorov-Smirnov statistic (max distance between the empirical CDFs)."""
    return float(ks_2samp(np.asarray(reference, float), np.asarray(current, float)).statistic)


def drift_report(
    reference: pd.DataFrame, current: pd.DataFrame, columns: list[str], params: dict[str, Any]
) -> dict[str, Any]:
    """Per-feature PSI/KS and the dataset-level alert (share of drifted features)."""
    rules = params["simulate"]
    features = {}
    for column in columns:
        value = psi(reference[column], current[column], rules["psi_bins"])
        features[column] = {
            "psi": value,
            "ks": ks_statistic(reference[column], current[column]),
            "drifted": value >= rules["psi_threshold"],
        }
    drifted = [c for c, f in features.items() if f["drifted"]]
    share = len(drifted) / len(columns)
    return {
        "features": features,
        "drifted_features": drifted,
        "drifted_share": share,
        "alert": share >= rules["drift_share_alert"],
        "max_psi": max(f["psi"] for f in features.values()),
    }


# --------------------------------------------------------------------------- scenarios


def _bounds(column: str, params: dict[str, Any]) -> tuple[float, float]:
    ranges = params["validation"]["ranges"]
    if column == "LIMIT_BAL":
        low, high = ranges["LIMIT_BAL"]
    elif column.startswith("PAY_AMT"):
        low, high = ranges["PAY_AMT"]
    elif column.startswith("PAY_"):
        low, high = ranges["PAY"]
    elif column.startswith("BILL_AMT"):
        low, high = ranges["BILL_AMT"]
    elif column == "EDUCATION":
        domain = params["validation"]["domains"]["EDUCATION"]
        low, high = min(domain), max(domain)
    else:
        raise ConfigError(f"no validation range for scenario column {column!r}")
    return float(low), float(high)


def apply_changes(
    frame: pd.DataFrame, changes: list[dict[str, Any]], params: dict[str, Any]
) -> pd.DataFrame:
    """Apply scenario changes (multiply/add), round to integers and clip to valid ranges."""
    out = frame.copy()
    for change in changes:
        for column in change["columns"]:
            if column not in RAW_INPUT_COLUMNS:
                raise ConfigError(f"scenario column {column!r} is not a model input")
            values = out[column].astype(float)
            values = (
                values * change["value"] if change["op"] == "multiply" else values + change["value"]
            )
            low, high = _bounds(column, params)
            out[column] = np.clip(np.rint(values), low, high).astype("int64")
    return out


def sample_batch(test: pd.DataFrame, size: int, seed: int, name: str) -> pd.DataFrame:
    """Seeded sample of test rows (with replacement only if the split is smaller than a batch)."""
    rng = make_rng(seed, f"drift:{name}")
    index = rng.choice(len(test), size=size, replace=size > len(test))
    return test.iloc[np.sort(index)].reset_index(drop=True)


def reference_summary(reference: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    quantiles = [0.05, 0.25, 0.5, 0.75, 0.95]
    return {
        c: {
            "mean": float(reference[c].mean()),
            "std": float(reference[c].std()),
            "min": float(reference[c].min()),
            "max": float(reference[c].max()),
            **{f"q{int(q * 100):02d}": float(reference[c].quantile(q)) for q in quantiles},
        }
        for c in columns
    }


def _auc(frame: pd.DataFrame, target: str) -> float | None:
    if frame[target].nunique() < 2:
        return None
    return float(roc_auc_score(frame[target], frame[SCORE_COLUMN]))


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    for needed in (model_path(params, smoke), metrics_path(params, smoke)):
        if not needed.is_file():
            raise IngestionError(f"{needed} not found (run train first)")
    target = params["data"]["target_column"]
    columns = model_input_columns(params)
    model = joblib.load(model_path(params, smoke))  # trusted, locally produced artifact only

    frames = {}
    for name in ("train", "test"):
        path = split_path(params, name, smoke)
        if not path.is_file():
            raise IngestionError(f"{path} not found (run the preprocess stage first)")
    frames = attach_feast_features(
        {n: pd.read_parquet(split_path(params, n, smoke)) for n in ("train", "test")}, params, smoke
    )

    def with_score(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame[[*columns, target]].copy()
        out[SCORE_COLUMN] = model.predict_proba(frame[columns])[:, 1]
        return out

    reference = with_score(frames["train"])
    write_parquet(reference_path(params, smoke), reference)
    test = frames["test"]
    rules = params["simulate"]
    monitored = [c for c in columns if c in RAW_INPUT_COLUMNS]

    scenarios, mismatches = {}, []
    for scenario in rules["scenarios"]:
        name = scenario["name"]
        batch = apply_changes(
            sample_batch(test, rules["batch_size"], params["seed"], name),
            scenario.get("changes") or [],
            params,
        )
        scored = with_score(batch)
        write_parquet(simulated_batches_dir(params, smoke) / f"{name}.parquet", scored)
        report = drift_report(reference, scored, monitored, params)
        ok = report["alert"] == scenario["expect_alert"]
        if not ok:
            mismatches.append(name)
        scenarios[name] = {
            "description": scenario["description"],
            "simulated": True,
            "n_rows": int(len(scored)),
            "changes": scenario.get("changes") or [],
            "expect_alert": scenario["expect_alert"],
            "alert": report["alert"],
            "matches_expectation": ok,
            "drifted_share": report["drifted_share"],
            "drifted_features": report["drifted_features"],
            "max_psi": report["max_psi"],
            "score_psi": psi(reference[SCORE_COLUMN], scored[SCORE_COLUMN], rules["psi_bins"]),
            "model_auc": _auc(scored, target),
            "mean_score": float(scored[SCORE_COLUMN].mean()),
            "features": report["features"],
        }

    result = {
        "simulated": True,
        "smoke": smoke,
        "thresholds": {k: rules[k] for k in ("psi_threshold", "drift_share_alert", "psi_bins")},
        "reference": {
            "path": str(reference_path(params, smoke).name),
            "n_rows": int(len(reference)),
            "mean_score": float(reference[SCORE_COLUMN].mean()),
            "model_auc_on_test": _auc(with_score(test), target),
            "features": reference_summary(reference, [*monitored, SCORE_COLUMN]),
        },
        "scenarios": scenarios,
        "all_match_expectation": not mismatches,
    }
    write_json(drift_report_path(params, smoke), result)
    if mismatches:
        raise PipelineError(f"drift scenarios did not behave as designed: {mismatches}")
    log.info("drift scenarios ok: %s", {n: s["alert"] for n, s in scenarios.items()})
    return {"scenarios": {n: s["alert"] for n, s in scenarios.items()}}


if __name__ == "__main__":
    raise SystemExit(
        stage_main(run, description="Build reference data and simulated drift batches")
    )
