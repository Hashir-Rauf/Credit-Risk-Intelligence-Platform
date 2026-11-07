"""Shared fixtures."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.common.config import load_params, repo_root


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
def iso_params(params: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    """params with every data path redirected into tmp_path and a small synthetic source,
    so tests never touch the repo's data/ or reports/ directories."""
    params["data"].update(
        source="synthetic",
        synthetic_rows=1500,
        raw_dir=str(tmp_path / "raw"),
        ingestion_report=str(tmp_path / "reports" / "ingestion_report.json"),
        local_path=str(tmp_path / "source.csv"),
    )
    params["validation"].update(
        report_path=str(tmp_path / "reports" / "validation_report.json"), min_rows=500
    )
    params["smoke"].update(data_dir=str(tmp_path / "smoke"), n_rows=800)
    return params


@pytest.fixture()
def valid_df(iso_params: dict[str, Any]):
    """A small, valid, UCI-shaped DataFrame."""
    from src.data.synthetic import make_synthetic_credit_data

    return make_synthetic_credit_data(1500, seed=1)
