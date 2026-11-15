"""MLflow setup and run provenance (rules.md §4: every run records algorithm, params, data
version, git commit, metrics, duration and artifacts)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import sklearn
import yaml

from src.common.config import repo_root, resolve_path
from src.common.io import read_json, sha256_file
from src.data.paths import provenance_path, raw_csv_path

FEAST_PLACEHOLDER = "n/a (Feast lands in Cycle 4)"


def tracking_uri(params: dict[str, Any], smoke: bool = False) -> str:
    """$MLFLOW_TRACKING_URI if set; else a local SQLite file (smoke runs: under data/smoke/)."""
    configured = os.environ.get(params["mlflow"]["tracking_uri_env"], "").strip()
    if configured:
        return configured
    if smoke:
        return "sqlite:///" + (resolve_path(params["smoke"]["data_dir"]) / "mlflow.db").as_posix()
    default = params["mlflow"]["default_tracking_uri"]
    prefix = "sqlite:///"
    if default.startswith(prefix) and not default.startswith(prefix + "/"):
        relative = default[len(prefix) :]
        if not (len(relative) > 1 and relative[1] == ":"):  # not a Windows drive path
            return prefix + (repo_root() / relative).as_posix()
    return default


def artifact_location(uri: str) -> str | None:
    """For a local SQLite store, keep artifacts in `mlartifacts/` next to the database so
    runs never scatter an `mlruns/` folder into the working directory. None for servers."""
    prefix = "sqlite:///"
    if not uri.startswith(prefix):
        return None
    return (Path(uri[len(prefix) :]).parent / "mlartifacts").as_uri()


def experiment_name(params: dict[str, Any], smoke: bool = False) -> str:
    name = params["mlflow"]["experiment_name"]
    return f"{name}-smoke" if smoke else name


def _git(*args: str) -> str | None:
    command = ["git", *args]  # fixed arguments from this module, no user input
    try:
        out = subprocess.run(  # noqa: S603
            command, cwd=repo_root(), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_info() -> dict[str, str]:
    """Commit SHA (env GIT_COMMIT_SHA wins, for containers without .git) and dirty flag."""
    sha = os.environ.get("GIT_COMMIT_SHA", "").strip() or _git("rev-parse", "HEAD") or "unknown"
    status = _git("status", "--porcelain")
    dirty = "unknown" if status is None else str(bool(status)).lower()
    return {"git_commit_sha": sha, "git_dirty": dirty}


def dvc_data_hash(
    params: dict[str, Any], smoke: bool = False, lock_path: Path | None = None
) -> str:
    """MD5 of the raw CSV recorded in dvc.lock by `dvc repro` (the DVC data version)."""
    if smoke:
        return "n/a (smoke run, synthetic data)"
    lock = lock_path or repo_root() / "dvc.lock"
    if not lock.is_file():
        return "unavailable (no dvc.lock: run `dvc repro`)"
    try:
        relative = raw_csv_path(params).relative_to(repo_root()).as_posix()
        stages = yaml.safe_load(lock.read_text(encoding="utf-8"))["stages"]
        for out in stages["ingest"]["outs"]:
            if out["path"] == relative:
                return str(out["md5"])
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        pass
    return "unavailable (raw data not found in dvc.lock)"


def data_provenance(params: dict[str, Any], smoke: bool = False) -> dict[str, str]:
    """`data_source` is `uci` for the real download and `synthetic` when the generator left its
    sidecar file (demos/tests only, never a reported result). `data_sha256` hashes the CSV."""
    csv = raw_csv_path(params, smoke)
    if not csv.is_file():
        return {"data_source": "unknown", "data_sha256": "unknown"}
    sidecar = provenance_path(params, smoke)
    source = str(read_json(sidecar)["source"]) if sidecar.is_file() else "uci"
    return {"data_source": source, "data_sha256": sha256_file(csv)}


def run_tags(params: dict[str, Any], smoke: bool, algorithm: str) -> dict[str, str]:
    """Tags every MLflow run carries (contract: docs/contracts-s1.md section 7)."""
    return {
        **git_info(),
        "dvc_data_hash": dvc_data_hash(params, smoke),
        **data_provenance(params, smoke),
        "feast_repo_commit": FEAST_PLACEHOLDER,
        "algorithm": algorithm,
        "seed": str(params["seed"]),
        "smoke": str(smoke).lower(),
        "sklearn_version": sklearn.__version__,
        "python_version": ".".join(str(v) for v in sys.version_info[:3]),
    }
