"""Live monitoring job: data quality, data drift and (simulated) live performance.

    python -m src.monitoring.drift once            # one evaluation, writes status.json and the HTML report
    python -m src.monitoring.drift serve           # keeps evaluating and exposes /metrics and /status

Reference = the training split's inputs and scores (`data/reference/reference.parquet`, written by
`src.models.drift`). Current = the pseudonymised prediction log of the running API
(`src.monitoring.prediction_log`). Per feature, drift is the Population Stability Index (the same definition as
`src.models.drift.psi`, a test keeps the two in step); the dataset alert fires when the share of drifted features is
at least `simulate.drift_share_alert` (0.3). Evidently, when installed, renders the human-readable HTML report that is
stored for the investigation checklist; the alert itself never depends on it.

Live performance needs labels. In this project they are SIMULATED: `scripts/simulate_drift.py` replays held-out rows
with their real outcomes as if the outcomes had arrived late. The status says so.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from src.monitoring.prediction_log import PredictionLog

log = logging.getLogger("credit_risk_monitor")

EPSILON = 1e-4
SCORE_COLUMN = "score"
LABEL_COLUMN = "default"


# --------------------------------------------------------------------------- statistics (mirror src.models.drift)


def _bucket_counts(reference: np.ndarray, current: np.ndarray, bins: int):
    values = np.unique(reference)
    if len(values) <= bins:
        ref = np.array([(reference == v).sum() for v in values] + [0])
        cur = np.array([(current == v).sum() for v in values] + [(~np.isin(current, values)).sum()])
        return ref, cur
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    return np.histogram(reference, edges)[0], np.histogram(current, edges)[0]


def psi(reference: Any, current: Any, bins: int = 10) -> float:
    """Population Stability Index (< 0.1 stable, 0.1-0.25 moderate, > 0.25 major)."""
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    if reference.size == 0 or current.size == 0:
        raise ValueError("reference and current must be non-empty")
    ref_counts, cur_counts = _bucket_counts(reference, current, bins)
    ref_share = np.maximum(ref_counts / ref_counts.sum(), EPSILON)
    cur_share = np.maximum(cur_counts / cur_counts.sum(), EPSILON)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def drift_summary(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    columns: list[str],
    bins: int = 10,
    psi_threshold: float = 0.2,
    share_alert: float = 0.3,
) -> dict[str, Any]:
    features = {}
    for column in columns:
        value = psi(reference[column], current[column], bins)
        features[column] = {"psi": value, "drifted": value >= psi_threshold}
    drifted = [c for c, f in features.items() if f["drifted"]]
    share = len(drifted) / len(columns)
    return {
        "features": features,
        "drifted_features": drifted,
        "drift_share": share,
        "drift_detected": share >= share_alert,
        "max_psi": max(f["psi"] for f in features.values()),
    }


# --------------------------------------------------------------------------- data quality


def data_quality(current: pd.DataFrame, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Cells that violate the feature schema the API validates against (missing, out of range, not allowed).

    The API rejects such requests, so a non-zero count points to an upstream change or a client bypassing
    validation: it is a data-quality alarm, not a model one.
    """
    failures: dict[str, int] = {}
    for spec in inputs:
        name = spec["name"]
        if name not in current:
            failures[name] = len(current)
            continue
        column = pd.to_numeric(current[name], errors="coerce")
        bad = column.isna()
        if "min" in spec:
            bad |= column < spec["min"]
        if "max" in spec:
            bad |= column > spec["max"]
        if "allowed" in spec:
            bad |= ~column.isin(spec["allowed"])
        if int(bad.sum()):
            failures[name] = int(bad.sum())
    return {"failed_cells": int(sum(failures.values())), "by_column": failures}


# --------------------------------------------------------------------------- live performance (simulated labels)


def _auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    positives = labels == 1
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def performance(
    rows: list[dict[str, Any]], threshold: float, window_s: int = 3600, min_rows: int = 50
) -> dict[str, Any]:
    """Live AUC and recall overall and per time window, from predictions joined with their labels."""
    if not rows:
        return {
            "labelled_rows": 0,
            "auc": None,
            "recall": None,
            "windows": [],
            "labels": "simulated",
        }
    frame = pd.DataFrame(rows)
    scores, labels = frame["score"].to_numpy(float), frame["label"].to_numpy(int)
    positives = labels == 1
    recall = float((scores[positives] >= threshold).mean()) if positives.any() else None
    windows = []
    frame["bucket"] = (frame["ts"] // window_s).astype(int)
    for bucket, part in frame.groupby("bucket"):
        if len(part) < min_rows:
            continue
        p_scores, p_labels = part["score"].to_numpy(float), part["label"].to_numpy(int)
        p_pos = p_labels == 1
        windows.append(
            {
                "start": int(bucket) * window_s,
                "rows": int(len(part)),
                "auc": _auc(p_labels, p_scores),
                "recall": float((p_scores[p_pos] >= threshold).mean()) if p_pos.any() else None,
            }
        )
    return {
        "labelled_rows": int(len(frame)),
        "auc": _auc(labels, scores),
        "recall": recall,
        "windows": windows,
        "labels": "simulated",
    }


# --------------------------------------------------------------------------- Evidently report (optional)


def write_html_report(
    reference: pd.DataFrame, current: pd.DataFrame, columns: list[str], path: Path
) -> bool:
    """Render the Evidently data-drift and data-summary report. Returns False when Evidently is not installed."""
    try:
        from evidently import Report
        from evidently.presets import DataDriftPreset, DataSummaryPreset
    except ImportError:
        log.warning("evidently is not installed: no HTML report (PSI metrics are still computed)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = Report([DataDriftPreset(), DataSummaryPreset()]).run(
        current[columns], reference[columns]
    )
    snapshot.save_html(str(path))
    return True


# --------------------------------------------------------------------------- orchestration


class Monitor:
    def __init__(
        self,
        store: PredictionLog,
        reference_path: Path,
        schema_path: Path,
        params_path: Path,
        out_dir: Path,
        window_hours: float = 24.0,
        min_rows: int = 200,
    ) -> None:
        self.store = store
        self.out_dir = out_dir
        self.window_s = window_hours * 3600
        self.min_rows = min_rows
        self.reference = pd.read_parquet(reference_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.inputs = schema["inputs"]
        self.columns = [spec["name"] for spec in self.inputs]
        self.threshold = float(schema.get("decision_threshold", 0.5))
        rules = yaml.safe_load(params_path.read_text(encoding="utf-8"))["simulate"]
        self.bins = int(rules["psi_bins"])
        self.psi_threshold = float(rules["psi_threshold"])
        self.share_alert = float(rules["drift_share_alert"])
        self.registry = CollectorRegistry()
        self.gauges = self._make_gauges()
        self.status: dict[str, Any] = {"state": "starting"}

    def _make_gauges(self) -> dict[str, Gauge]:
        def gauge(name: str, doc: str, labels: tuple[str, ...] = ()) -> Gauge:
            return Gauge(name, doc, labels, registry=self.registry)

        return {
            "window_rows": gauge("monitor_window_rows", "Predictions in the evaluated window"),
            "ready": gauge(
                "monitor_window_sufficient", "1 when the window has enough rows to judge drift"
            ),
            "share": gauge(
                "monitor_drift_share", "Share of input features whose PSI is above the threshold"
            ),
            "detected": gauge(
                "monitor_drift_detected", "1 when the drift share is at or above the alert level"
            ),
            "max_psi": gauge("monitor_max_feature_psi", "Largest per-feature PSI"),
            "psi": gauge("monitor_feature_psi", "PSI per input feature", ("feature",)),
            "score_psi": gauge(
                "monitor_score_psi", "PSI of the returned risk score against training"
            ),
            "quality": gauge(
                "monitor_data_quality_failed_cells", "Input cells violating the feature schema"
            ),
            "auc": gauge(
                "monitor_live_auc", "Live ROC-AUC on labelled predictions (labels are simulated)"
            ),
            "recall": gauge(
                "monitor_live_recall", "Live recall on labelled predictions (labels are simulated)"
            ),
            "labelled": gauge(
                "monitor_labelled_rows", "Predictions whose (simulated) label has arrived"
            ),
            "last_run": gauge(
                "monitor_last_run_timestamp_seconds", "Unix time of the last evaluation"
            ),
        }

    def evaluate(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        rows = self.store.recent(since=now - self.window_s)
        gauges = self.gauges
        gauges["window_rows"].set(len(rows))
        gauges["last_run"].set(now)
        status: dict[str, Any] = {
            "generated_at": now,
            "window_rows": len(rows),
            "min_rows": self.min_rows,
            "reference_rows": int(len(self.reference)),
            "model_versions": sorted({r["model_version"] for r in rows}),
        }
        labelled = self.store.labelled(since=now - self.window_s)
        perf = performance(labelled, self.threshold)
        status["performance"] = perf
        gauges["labelled"].set(perf["labelled_rows"])
        for key, name in (("auc", "auc"), ("recall", "recall")):
            if perf[key] is not None:
                gauges[name].set(perf[key])
        if len(rows) < self.min_rows:
            gauges["ready"].set(0)
            gauges["share"].set(0)  # nothing to judge: never leave a stale alert level behind
            gauges["detected"].set(0)
            status.update(state="insufficient_data", drift_detected=False)
            self.status = status
            return status
        gauges["ready"].set(1)
        current = pd.DataFrame([{**r["features"], SCORE_COLUMN: r["score"]} for r in rows])
        summary = drift_summary(
            self.reference, current, self.columns, self.bins, self.psi_threshold, self.share_alert
        )
        quality = data_quality(current, self.inputs)
        score_psi = psi(self.reference[SCORE_COLUMN], current[SCORE_COLUMN], self.bins)
        gauges["share"].set(summary["drift_share"])
        gauges["detected"].set(1 if summary["drift_detected"] else 0)
        gauges["max_psi"].set(summary["max_psi"])
        gauges["score_psi"].set(score_psi)
        gauges["quality"].set(quality["failed_cells"])
        for name, feature in summary["features"].items():
            gauges["psi"].labels(name).set(feature["psi"])
        report = self.out_dir / "drift_report.html"
        status.update(
            state="drift" if summary["drift_detected"] else "ok",
            drift_detected=summary["drift_detected"],
            drift_share=summary["drift_share"],
            drifted_features=summary["drifted_features"],
            max_psi=summary["max_psi"],
            score_psi=score_psi,
            feature_psi={k: v["psi"] for k, v in summary["features"].items()},
            data_quality=quality,
            html_report=(
                str(report)
                if write_html_report(self.reference, current, self.columns, report)
                else None
            ),
        )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "status.json").write_text(
            json.dumps(status, indent=2, default=str), encoding="utf-8"
        )
        self.status = status
        return status

    # ---------------------------------------------------------------- HTTP exposition
    def serve(self, port: int, interval_s: int, host: str = "127.0.0.1") -> None:
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/metrics":
                    body, kind = generate_latest(monitor.registry), CONTENT_TYPE_LATEST
                elif self.path == "/status":
                    body, kind = (
                        json.dumps(monitor.status, default=str).encode(),
                        "application/json",
                    )
                elif self.path == "/report":
                    html = monitor.out_dir / "drift_report.html"
                    if not html.exists():
                        self.send_error(404)
                        return
                    body, kind = html.read_bytes(), "text/html"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                return

        server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("monitor listening on :%s", port)
        while True:
            try:
                state = self.evaluate()
                log.info("evaluated: %s (%s rows)", state.get("state"), state.get("window_rows"))
            except Exception:
                log.exception("evaluation failed")
            time.sleep(interval_s)


def build_monitor(env: dict[str, str] | None = None) -> Monitor:
    env = dict(os.environ if env is None else env)
    path = env.get("PREDICTION_LOG_PATH")
    if not path:
        raise SystemExit("PREDICTION_LOG_PATH is required")
    return Monitor(
        PredictionLog(path, int(env.get("PREDICTION_LOG_RETENTION_DAYS", "30"))),
        Path(env.get("REFERENCE_PATH", "data/reference/reference.parquet")),
        Path(env.get("FEATURE_SCHEMA_PATH", "reports/feature_schema.json")),
        Path(env.get("PARAMS_PATH", "params.yaml")),
        Path(env.get("MONITOR_OUT_DIR", "reports/monitoring")),
        float(env.get("MONITOR_WINDOW_HOURS", "24")),
        int(env.get("MONITOR_MIN_ROWS", "200")),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["once", "serve", "labels"])
    parser.add_argument(
        "file",
        nargs="?",
        help="labels: JSON-lines file of {request_id, idx, label}, or - for stdin",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("MONITOR_PORT", "9101")))
    parser.add_argument(
        "--interval", type=int, default=int(os.environ.get("MONITOR_INTERVAL_S", "30"))
    )
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    monitor = build_monitor()
    if args.command == "labels":
        import sys

        text = sys.stdin.read() if args.file in (None, "-") else Path(args.file).read_text("utf-8")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        added = monitor.store.add_labels((r["request_id"], r["idx"], r["label"]) for r in rows)
        print(f"stored {added} SIMULATED labels")
    elif args.command == "once":
        print(json.dumps(monitor.evaluate(), indent=2, default=str))
    else:
        monitor.serve(args.port, args.interval, os.environ.get("MONITOR_HOST", "127.0.0.1"))


if __name__ == "__main__":
    main()
