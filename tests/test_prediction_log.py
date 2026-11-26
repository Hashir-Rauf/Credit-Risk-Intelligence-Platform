"""Prediction log store (Phase 8): retention, pseudonyms, label join and S3 export."""

from __future__ import annotations

import gzip
import json

from src.monitoring.prediction_log import PredictionLog, hash_customer

DAY = 86_400


def make_log(tmp_path, **kwargs) -> PredictionLog:
    return PredictionLog(tmp_path / "p.db", salt="pepper", **kwargs)


def test_hash_is_keyed_and_stable():
    assert hash_customer("c1", "a") == hash_customer("c1", "a")
    assert hash_customer("c1", "a") != hash_customer("c1", "b")
    assert hash_customer(None, "a") is None and "c1" not in hash_customer("c1", "a")


def test_retention_removes_old_rows(tmp_path):
    store = make_log(tmp_path, retention_days=7)
    store.record("old", [{"x": 1}], [0.2], "1", now=1_000_000.0)
    store.record("new", [{"x": 2}], [0.4], "1", now=1_000_000.0 + 10 * DAY)
    assert store.purge(now=1_000_000.0 + 10 * DAY) == 1
    assert [r["request_id"] for r in store.recent()] == ["new"]


def test_labels_join_only_matching_predictions(tmp_path):
    store = make_log(tmp_path)
    store.record("r1", [{"x": 1}, {"x": 2}], [0.9, 0.1], "1")
    store.add_labels([("r1", 0, 1), ("r1", 1, 0), ("unknown", 0, 1)])
    joined = store.labelled()
    assert [(j["idx"], j["label"]) for j in joined] == [(0, 1), (1, 0)]


def test_export_uploads_each_row_once_as_gzip_lines(tmp_path):
    class FakeS3:
        def __init__(self):
            self.objects = {}

        def put_object(self, Bucket, Key, Body, **kwargs):  # noqa: N803 (boto3 signature)
            self.objects[(Bucket, Key)] = Body

    store = make_log(tmp_path)
    store.record("r1", [{"x": 1}, {"x": 2}], [0.9, 0.1], "3", customer_id="c9")
    client = FakeS3()
    assert store.export_new(client, "bucket") == 2
    assert store.export_new(client, "bucket") == 0  # already exported
    (((bucket, key), body),) = client.objects.items()
    assert bucket == "bucket" and key.startswith("prediction-logs/dt=")
    lines = [json.loads(line) for line in gzip.decompress(body).decode().splitlines()]
    assert [line["score"] for line in lines] == [0.9, 0.1]
    assert all("c9" not in json.dumps(line) for line in lines)
