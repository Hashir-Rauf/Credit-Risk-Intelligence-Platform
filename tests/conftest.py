"""Shared fixtures."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from src.common.config import load_params, repo_root

STAGE_ORDER = ("data", "preprocess", "feast", "tune", "train", "evaluate")


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
    # Tests default to the fast baseline; tuning tests switch to `tuned` explicitly.
    params["train"]["algorithm"] = "logistic_regression"
    # Feast is exercised by its own tests; the other tests read the parquet splits directly (fast).
    params["features"]["use_feast_for_training"] = False
    params["feast"].update(
        data_dir=str(base / "feast"),
        report_path=str(base / "reports" / "feast_report.json"),
        parity_sample=50,
    )
    params["tune"].update(
        n_trials=3,
        smoke_n_trials=2,
        results_path=str(base / "reports" / "tuning.json"),
        comparison_path=str(base / "reports" / "model_comparison.md"),
    )
    params["promotion"].update(
        decision_path=str(base / "reports" / "promotion.json"),
        registration_path=str(base / "reports" / "registration.json"),
    )
    params["fairness"].update(
        min_group_size=20, report_path=str(base / "reports" / "fairness.json")
    )
    params["explain"].update(
        background_size=30,
        importance_sample=30,
        report_path=str(base / "reports" / "explanations.json"),
        background_path=str(base / "models" / "explainer_background.parquet"),
    )
    params["simulate"].update(
        batch_size=400,
        reference_path=str(base / "reference" / "reference.parquet"),
        batches_dir=str(base / "simulated"),
        report_path=str(base / "reports" / "drift_scenarios.json"),
    )
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
    from src.features import feast_features, preprocessing
    from src.models import evaluate, train, tune

    runners = {
        "data": lambda p, s: make_data(p, s),
        "preprocess": preprocessing.run,
        "feast": feast_features.run,
        "tune": tune.run,
        "train": train.run,
        "evaluate": evaluate.run,
    }
    results: dict[str, Any] = {}
    for name in STAGE_ORDER:
        if name == "tune" and params["train"]["algorithm"] != "tuned" and upto != "tune":
            continue  # tuning only feeds `train` when train.algorithm is "tuned"
        if name == "feast" and not params["features"]["use_feast_for_training"] and upto != "feast":
            continue  # the feature store is only needed when training reads from it
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
