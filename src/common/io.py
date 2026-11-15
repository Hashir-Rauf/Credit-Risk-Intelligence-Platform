"""Deterministic file helpers.

DVC hashes output files, so writes must be byte-identical across operating systems: always
LF line endings, sorted JSON keys, no timestamps.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file then rename, so a crash never leaves a half-written output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json(path: Path, obj: Any) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_parquet(path: Path, frame: pd.DataFrame) -> None:
    """Parquet via a temp file + rename (no index, deterministic column order)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False, engine="pyarrow")
    os.replace(tmp, path)
