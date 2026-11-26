"""Pseudonymised prediction log: the raw material for drift detection and delayed-label evaluation.

What is stored per scored record: request id, a keyed hash of the customer id (when the caller
sends an `X-Customer-Id` header), the timestamp, the model version, the score and the model inputs
that drift detection needs. The inputs are the 20 numeric fields the API accepts; the sensitive
attributes (SEX, MARRIAGE, AGE) are not API inputs, so they are never stored. There is no name,
e-mail or raw customer id.

Storage is a local SQLite file (retention enforced by deleting old rows). When an S3 destination
is configured the rows not yet exported are written as gzip-compressed JSON lines under
`prediction-logs/` so that the monitoring job can read them from a container that does not share
the API's disk. Logging must never break scoring: every failure is swallowed and counted.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger("credit_risk_api")

PURGE_EVERY = 200  # inserts between retention sweeps
SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    customer_hash TEXT,
    ts REAL NOT NULL,
    model_version TEXT NOT NULL,
    score REAL NOT NULL,
    features TEXT NOT NULL,
    exported INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS predictions_ts ON predictions (ts);
CREATE TABLE IF NOT EXISTS labels (
    request_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    label INTEGER NOT NULL,
    labelled_at REAL NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (request_id, idx)
);
"""


def hash_customer(customer_id: str | None, salt: str) -> str | None:
    """Keyed, one-way pseudonym. Without the salt the id cannot be recovered by hashing guesses."""
    if not customer_id:
        return None
    return hmac.new(salt.encode(), customer_id.encode(), hashlib.sha256).hexdigest()[:32]


class PredictionLog:
    def __init__(self, path: Path | str, retention_days: int = 30, salt: str = "") -> None:
        self.path = Path(path)
        self.retention_s = retention_days * 86_400
        self.salt = salt
        self.failures = 0
        self._lock = threading.Lock()
        self._since_purge = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self.purge()

    # ------------------------------------------------------------------ writing

    def record(
        self,
        request_id: str,
        records: Sequence[Mapping[str, Any]],
        scores: Iterable[float],
        model_version: str,
        customer_id: str | None = None,
        now: float | None = None,
    ) -> None:
        """Append one row per scored record. Never raises."""
        stamp = time.time() if now is None else now
        try:
            rows = []
            for index, (record, score) in enumerate(zip(records, scores, strict=True)):
                owner = f"{customer_id}:{index}" if customer_id else None
                rows.append(
                    (
                        request_id,
                        index,
                        hash_customer(owner, self.salt),
                        stamp,
                        model_version,
                        float(score),
                        json.dumps({k: _plain(v) for k, v in record.items()}, sort_keys=True),
                    )
                )
            with self._lock:
                self._db.executemany(
                    "INSERT INTO predictions (request_id, idx, customer_hash, ts, model_version, score, features)"  # noqa: E501
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._since_purge += len(rows)
                if self._since_purge >= PURGE_EVERY:
                    self._purge_locked(stamp)
        except Exception:  # logging must never fail a prediction
            self.failures += 1
            log.warning("prediction log write failed", exc_info=True)

    def purge(self, now: float | None = None) -> int:
        with self._lock:
            return self._purge_locked(time.time() if now is None else now)

    def _purge_locked(self, now: float) -> int:
        self._since_purge = 0
        cutoff = now - self.retention_s
        deleted = self._db.execute("DELETE FROM predictions WHERE ts < ?", (cutoff,)).rowcount
        self._db.execute("DELETE FROM labels WHERE labelled_at < ?", (cutoff,))
        return deleted

    # ------------------------------------------------------------------ reading (monitoring job)

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0])

    def recent(self, since: float | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT request_id, idx, customer_hash, ts, model_version, score, features FROM predictions"  # noqa: E501
        args: list[Any] = []
        if since is not None:
            sql += " WHERE ts >= ?"
            args.append(since)
        sql += " ORDER BY ts DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            cursor = self._db.execute(sql, args)
            names = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(zip(names, row, strict=True))
            item["features"] = json.loads(item["features"])
            out.append(item)
        return out

    def add_labels(self, labels: Iterable[tuple[str, int, int]], simulated: bool = True) -> int:
        """Store (request_id, idx, label). The label source here is SIMULATED (held-out replay)."""
        stamp = time.time()
        rows = [(r, i, int(label), stamp, int(simulated)) for r, i, label in labels]
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO labels (request_id, idx, label, labelled_at, simulated) VALUES (?, ?, ?, ?, ?)",  # noqa: E501
                rows,
            )
        return len(rows)

    def labelled(self, since: float | None = None) -> list[dict[str, Any]]:
        """Predictions joined with their labels (inner join: only records whose label has arrived)."""  # noqa: E501
        sql = (
            "SELECT p.request_id, p.idx, p.ts, p.model_version, p.score, l.label "
            "FROM predictions p JOIN labels l ON l.request_id = p.request_id AND l.idx = p.idx"
        )
        args: list[Any] = []
        if since is not None:
            sql += " WHERE p.ts >= ?"
            args.append(since)
        sql += " ORDER BY p.ts"
        with self._lock:
            cursor = self._db.execute(sql, args)
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    # ------------------------------------------------------------------ export to S3

    def export_new(
        self, client: Any, bucket: str, prefix: str = "prediction-logs", batch: int = 5000
    ) -> int:
        """Upload rows not exported yet as one gzip JSON-lines object. Returns the number of rows exported."""  # noqa: E501
        with self._lock:
            cursor = self._db.execute(
                "SELECT id, request_id, idx, customer_hash, ts, model_version, score, features FROM predictions "  # noqa: E501
                "WHERE exported = 0 ORDER BY id LIMIT ?",
                (batch,),
            )
            rows = cursor.fetchall()
        if not rows:
            return 0
        lines = [
            json.dumps(
                {
                    "request_id": r[1],
                    "idx": r[2],
                    "customer_hash": r[3],
                    "ts": r[4],
                    "model_version": r[5],
                    "score": r[6],
                    "features": json.loads(r[7]),
                },
                sort_keys=True,
            )
            for r in rows
        ]
        day = time.strftime("%Y-%m-%d", time.gmtime(rows[-1][4]))
        key = f"{prefix.strip('/')}/dt={day}/{int(rows[0][4])}-{rows[0][0]}-{rows[-1][0]}.jsonl.gz"
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=gzip.compress(("\n".join(lines) + "\n").encode()),
            ServerSideEncryption="AES256",
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
        )
        with self._lock:
            self._db.execute(
                "UPDATE predictions SET exported = 1 WHERE id BETWEEN ? AND ?",
                (rows[0][0], rows[-1][0]),
            )
        return len(rows)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _plain(value: Any) -> Any:
    """JSON-safe scalar (numpy numbers arrive from validation)."""
    if hasattr(value, "item"):
        return value.item()
    return value
