"""Monitoring job (Phase 8): drift statistics, data quality, live performance, evaluation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import recall_score, roc_auc_score
from src.common.config import load_params
from src.data.synthetic import make_synthetic_credit_data
from src.features.preprocessing import build_feature_schema, model_input_columns
from src.models import drift as s1_drift
from src.monitoring import drift
from src.monitoring.prediction_log import PredictionLog

ROOT = Path(__file__).resolve().parents[1]


def test_psi_matches_the_modelling_implementation():
    rng = np.random.default_rng(0)
    reference, shifted = rng.normal(0, 1, 5000), rng.normal(0.8, 1.3, 1000)
    assert drift.psi(reference, shifted) == pytest.approx(s1_drift.psi(reference, shifted))
    codes = rng.integers(-1, 4, 2000)
    assert drift.psi(codes, codes + 2) == pytest.approx(s1_drift.psi(codes, codes + 2))


def test_same_distribution_is_stable_and_a_shift_is_flagged():
    rng = np.random.default_rng(1)
    reference = pd.DataFrame({"a": rng.normal(size=4000), "b": rng.normal(size=4000)})
    same = pd.DataFrame({"a": rng.normal(size=800), "b": rng.normal(size=800)})
    moved = pd.DataFrame({"a": rng.normal(2, 1, 800), "b": rng.normal(2, 1, 800)})
    assert not drift.drift_summary(reference, same, ["a", "b"])["drift_detected"]
    result = drift.drift_summary(reference, moved, ["a", "b"])
    assert result["drift_detected"] and result["drift_share"] == 1.0


def test_data_quality_counts_schema_violations():
    inputs = [
        {"name": "AGE", "dtype": "integer", "min": 18, "max": 100},
        {"name": "EDUCATION", "dtype": "integer", "allowed": [1, 2, 3]},
    ]
    frame = pd.DataFrame({"AGE": [30, 17, None, 120], "EDUCATION": [1, 2, 9, 3]})
    result = drift.data_quality(frame, inputs)
    assert result["by_column"] == {"AGE": 3, "EDUCATION": 1}
    assert result["failed_cells"] == 4


def test_performance_matches_sklearn_and_buckets_by_window():
    rng = np.random.default_rng(2)
    labels = rng.integers(0, 2, 400)
    scores = np.clip(labels * 0.3 + rng.normal(0.35, 0.2, 400), 0, 1)
    rows = [
        {"ts": 10.0 + (i // 200) * 3600, "score": float(s), "label": int(y)}
        for i, (s, y) in enumerate(zip(scores, labels, strict=True))
    ]
    result = drift.performance(rows, threshold=0.5, window_s=3600, min_rows=50)
    assert result["auc"] == pytest.approx(roc_auc_score(labels, scores))
    assert result["recall"] == pytest.approx(recall_score(labels, scores >= 0.5))
    assert len(result["windows"]) == 2 and result["labels"] == "simulated"


@pytest.fixture()
def setup(tmp_path):
    params = load_params()
    columns = model_input_columns(params)
    frame = make_synthetic_credit_data(3000, seed=5)
    reference = frame[columns].copy()
    reference["score"] = np.clip(reference["PAY_1"] * 0.1 + 0.3, 0, 1)
    reference["default"] = frame[params["data"]["target_column"]]
    reference.to_parquet(tmp_path / "reference.parquet")
    (tmp_path / "schema.json").write_text(
        json.dumps(build_feature_schema(params)), encoding="utf-8"
    )
    shutil.copy(ROOT / "params.yaml", tmp_path / "params.yaml")
    store = PredictionLog(tmp_path / "p.db", salt="x")
    monitor = drift.Monitor(
        store,
        tmp_path / "reference.parquet",
        tmp_path / "schema.json",
        tmp_path / "params.yaml",
        tmp_path / "out",
        window_hours=24,
        min_rows=100,
    )
    return {
        "frame": frame,
        "columns": columns,
        "store": store,
        "monitor": monitor,
        "ref": reference,
    }


def log_rows(setup, frame, label=False):
    records = frame[setup["columns"]].to_dict("records")
    scores = np.clip(frame["PAY_1"].to_numpy() * 0.1 + 0.3, 0, 1)
    setup["store"].record("req-" + str(len(records)) + str(id(frame)), records, scores, "7")
    return records, scores


def test_too_little_data_is_reported_not_judged(setup):
    log_rows(setup, setup["frame"].iloc[:20])
    status = setup["monitor"].evaluate()
    assert status["state"] == "insufficient_data" and status["drift_detected"] is False


def test_in_distribution_traffic_is_ok(setup):
    log_rows(setup, setup["frame"].iloc[1000:1500])
    status = setup["monitor"].evaluate()
    assert status["state"] == "ok" and status["data_quality"]["failed_cells"] == 0


def test_shifted_traffic_raises_the_drift_alert_and_the_metrics(setup):
    shifted = setup["frame"].iloc[1000:1500].copy()
    for column in [c for c in setup["columns"] if c.startswith("PAY_") and "AMT" not in c]:
        shifted[column] = shifted[column] + 2
    shifted["LIMIT_BAL"] = shifted["LIMIT_BAL"] * 1.6
    log_rows(setup, shifted)
    status = setup["monitor"].evaluate()
    assert status["state"] == "drift" and status["drift_detected"] is True
    assert "PAY_1" in status["drifted_features"]
    exposed = drift.generate_latest(setup["monitor"].registry).decode()
    assert (
        "monitor_drift_detected 1.0" in exposed
        and 'monitor_feature_psi{feature="PAY_1"}' in exposed
    )


def test_labels_produce_live_performance(setup):
    frame = setup["frame"].iloc[1000:1400]
    records, scores = log_rows(setup, frame)
    request_id = setup["store"].recent()[0]["request_id"]
    target = setup["monitor"].reference["default"].iloc[:0]  # keep the reference untouched
    assert len(target) == 0
    labels = frame[load_params()["data"]["target_column"]].to_numpy()
    setup["store"].add_labels((request_id, i, int(y)) for i, y in enumerate(labels))
    status = setup["monitor"].evaluate()
    assert status["performance"]["labelled_rows"] == 400
    assert status["performance"]["auc"] == pytest.approx(roc_auc_score(labels, scores))
