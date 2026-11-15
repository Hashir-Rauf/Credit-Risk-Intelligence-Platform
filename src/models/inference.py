"""Scoring helper for the serving layer (Flask / BentoML / Kubeflow).

The saved pipeline already contains feature engineering, so scoring is: raw fields in,
probability out, through exactly the code path used in training. Request validation (types,
ranges, status codes) belongs to the API layer and should be generated from
`reports/feature_schema.json`; the checks here are a last line of defence.

SECURITY: `load_model` unpickles the file. Only load models produced by our own pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.features.preprocessing import RAW_INPUT_COLUMNS


def load_model(path: str | Path) -> Any:
    return joblib.load(path)


def records_to_frame(
    records: Sequence[Mapping[str, Any]], columns: Sequence[str] = RAW_INPUT_COLUMNS
) -> pd.DataFrame:
    """Rows of raw fields -> numeric DataFrame with exactly the model's input columns.
    Unknown keys (e.g. a client sending SEX) are ignored, never used."""
    if not records:
        raise ValueError("no records to score")
    frame = pd.DataFrame(list(records))
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required fields: {missing}")
    selected = frame[list(columns)]
    try:
        numeric = selected.apply(pd.to_numeric, errors="raise")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"all fields must be numeric: {exc}") from exc
    if numeric.isna().any().any():
        raise ValueError("fields must not be null")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("fields must be finite numbers")
    return numeric


def predict_proba(
    model: Any, records: Sequence[Mapping[str, Any]], columns: Sequence[str] = RAW_INPUT_COLUMNS
) -> np.ndarray:
    return model.predict_proba(records_to_frame(records, columns))[:, 1]


def predict(
    model: Any,
    records: Sequence[Mapping[str, Any]],
    threshold: float,
    columns: Sequence[str] = RAW_INPUT_COLUMNS,
) -> list[dict[str, Any]]:
    """[{probability, prediction}] where prediction = 1 (default) if probability >= threshold."""
    return [
        {"probability": float(p), "prediction": int(p >= threshold)}
        for p in predict_proba(model, records, columns)
    ]
