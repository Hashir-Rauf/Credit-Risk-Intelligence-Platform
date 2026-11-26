"""Controlled, SIMULATED drift scenario against a running API.

Replays one of the prepared batches (`data/simulated/<scenario>.parquet`, made by `python -m src.models.drift`) through
POST /predict in chunks, so the prediction log, the dashboard, the drift alert and the retrain trigger all see it. The
batches are resampled held-out test rows with shifted features (`params.yaml` -> `simulate.scenarios`); labels are the
real outcomes of those rows and are written to a JSON-lines file that stands in for ground truth arriving late.

    python scripts/simulate_drift.py --scenario no_drift                # normal traffic
    python scripts/simulate_drift.py --scenario payment_delays          # inflated PAY_* and halved payments
    docker compose exec -T monitor python -m src.monitoring.drift labels - < reports/monitoring/labels.jsonl

Environment: API_KEY (required), API_URL (default http://127.0.0.1:8000).
Everything this script sends is simulated data and must be presented as such.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 50


def post(url: str, key: str, records: list[dict]) -> dict:
    request = urllib.request.Request(
        f"{url}/predict",
        data=json.dumps({"records": records}).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=60
    ) as reply:  # noqa: S310 (operator-supplied http(s) URL)
        return json.loads(reply.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scenario", default="payment_delays")
    parser.add_argument(
        "--rounds", type=int, default=1, help="times to replay the batch (more rows for the window)"
    )
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between chunks")
    parser.add_argument("--labels-out", type=Path, default=ROOT / "reports/monitoring/labels.jsonl")
    args = parser.parse_args()

    key = os.environ.get("API_KEY")
    if not key:
        print("set API_KEY", file=sys.stderr)
        return 2
    url = os.environ.get("API_URL", "http://127.0.0.1:8000").rstrip("/")
    batch_path = ROOT / "data/simulated" / f"{args.scenario}.parquet"
    if not batch_path.exists():
        print(
            f"missing {batch_path}: run `python -m src.models.drift` (dvc pull) first",
            file=sys.stderr,
        )
        return 2
    schema = json.loads((ROOT / "reports/feature_schema.json").read_text(encoding="utf-8"))
    columns = [spec["name"] for spec in schema["inputs"]]
    frame = pd.read_parquet(batch_path)
    labels_out: list[dict] = []
    sent = 0
    for _ in range(args.rounds):
        for start in range(0, len(frame), CHUNK):
            chunk = frame.iloc[start : start + CHUNK]
            try:
                reply = post(url, key, chunk[columns].to_dict("records"))
            except urllib.error.URLError as error:
                print(f"request failed: {error}", file=sys.stderr)
                return 1
            for index, label in enumerate(chunk["default"].astype(int)):
                labels_out.append(
                    {"request_id": reply["request_id"], "idx": index, "label": int(label)}
                )
            sent += len(chunk)
            if args.pause:
                time.sleep(args.pause)
    args.labels_out.parent.mkdir(parents=True, exist_ok=True)
    args.labels_out.write_text(
        "\n".join(json.dumps(item) for item in labels_out) + "\n", encoding="utf-8"
    )
    print(
        f"scenario={args.scenario} (SIMULATED) sent={sent} records; labels written to {args.labels_out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
