"""Feast definitions for the `feast` CLI. Single source of truth is
src/features/feast_features.py (`build_definitions`); this file only exposes its objects.

    cd feature_repo
    PYTHONPATH=.. python -m feast apply          # PowerShell: $env:PYTHONPATH=".."

Run `python -m src.features.feast_features` first: it writes the parquet sources.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import load_params  # noqa: E402
from src.features.feast_features import build_definitions  # noqa: E402

_params = load_params()
entity, payment_history, billing, account = build_definitions(_params)
