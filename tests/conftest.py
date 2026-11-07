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
