"""Shared fixtures."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from src.common.config import load_params, repo_root

STAGE_ORDER = ("data", "preprocess", "train", "evaluate")


def isolate_params(params: dict[str, Any], base: Path) -> dict[str, Any]:
    """Redirect every data/report/model path into `base`, so tests never touch the repo's
    data/, reports/ or models/ directories."""
    params["data"].update(
        raw_path=str(base / "raw" / "credit_default.csv"),
        validation_report=str(base / "reports" / "data_validation.json"),
        processed_dir=str(base / "processed"),
    )
    params["features"]["schema_path"] = str(base / "reports" / "feature_schema.json")
    params["train"].update(
        model_path=str(base / "models" / "model.joblib"),
        metrics_path=str(base / "reports" / "metrics.json"),
        plots_dir=str(base / "reports" / "plots"),
        last_run_path=str(base / "reports" / "last_run.json"),
    )
    params["evaluate"]["evaluation_path"] = str(base / "reports" / "evaluation.json")
    params["smoke"].update(data_dir=str(base / "smoke"), n_rows=800)
    return params


def make_data(params: dict[str, Any], smoke: bool = False, rows: int = 1500) -> dict[str, Any]:
    """Synthetic raw CSV + validation report (stands in for the ingest and validate stages)."""
    from src.data import synthetic

    return synthetic.run(params, smoke, n_rows=rows)


def run_stages(
    params: dict[str, Any], upto: str = "evaluate", smoke: bool = False
) -> dict[str, Any]:
    """Run the pipeline stages in order (in-process) up to and including `upto`."""
    from src.features import preprocessing
    from src.models import evaluate, train

    runners = {
        "data": lambda p, s: make_data(p, s),
        "preprocess": preprocessing.run,
        "train": train.run,
        "evaluate": evaluate.run,
    }
    results: dict[str, Any] = {}
    for name in STAGE_ORDER:
        results[name] = runners[name](params, smoke)
        if name == upto:
            break
    return results


def use_local_mlflow(monkeypatch: pytest.MonkeyPatch, base: Path) -> str:
    uri = f"sqlite:///{(base / 'mlflow.db').as_posix()}"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    return uri


@pytest.fixture(scope="session")
def root() -> Path:
    return repo_root()


@pytest.fixture()
def params() -> dict[str, Any]:
    """A fresh, valid copy of the real params.yaml (safe to mutate)."""
    return copy.deepcopy(load_params())


@pytest.fixture()
def write_params(tmp_path: Path):
    """Write a params dict to a temp YAML file and return its path."""

    def _write(data: dict[str, Any], name: str = "params.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    return _write


@pytest.fixture()
def iso_params(params: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated params (see isolate_params) plus a throwaway local MLflow store."""
    use_local_mlflow(monkeypatch, tmp_path)
    return isolate_params(params, tmp_path)


@pytest.fixture()
def valid_df(iso_params: dict[str, Any]):
    """A small, valid, UCI-shaped DataFrame."""
    from src.data.synthetic import make_synthetic_credit_data

    return make_synthetic_credit_data(1500, seed=1)


@pytest.fixture(scope="module")
def pipeline_env(tmp_path_factory: pytest.TempPathFactory):
    """The whole pipeline (ingest -> evaluate) run once per test module on synthetic data.
    Read-only: tests that change anything must build their own copy with iso_params."""
    base = tmp_path_factory.mktemp("pipeline")
    with pytest.MonkeyPatch.context() as patch:
        uri = use_local_mlflow(patch, base)
        params = isolate_params(copy.deepcopy(load_params()), base)
        results = run_stages(params)
        yield SimpleNamespace(params=params, base=base, tracking_uri=uri, results=results)
