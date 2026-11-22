"""Per-prediction explanations and global feature importance.

    python -m src.models.explain [--params params.yaml] [--smoke]

Contributions are in **log-odds** and explain the model's own features (the engineered and
scaled/one-hot columns it actually uses, e.g. `pay_delay_max`, `util_last`), relative to a
background sample of training rows:

    log_odds(customer) = base_value + sum(contribution_j)          (exact, tested)

* Logistic regression: exact linear contributions `coef_j * (x_j - mean_background_j)`, which
  are the SHAP values of a linear model with independent features. No SHAP dependency needed.
* Any other model (random forest, gradient boosting): SHAP permutation explainer
  (`shap.Explainer(..., algorithm="permutation")`), model agnostic and exactly additive.

Positive contribution = pushes the customer towards default (higher risk). The stage writes
`models/explainer_background.parquet` (serving needs it next to `model.joblib`),
`reports/explanations.json` (global importance + one worked example) and a bar chart.
Explanations describe the MODEL, not causes in the real world.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from src.common.cli import stage_main
from src.common.errors import ConfigError, IngestionError
from src.common.io import atomic_write_bytes, write_json, write_parquet
from src.common.logging_utils import get_logger
from src.common.seed import derive_seed, make_rng
from src.data.paths import (
    explainer_background_path,
    explanations_path,
    metrics_path,
    model_path,
    split_path,
)
from src.features.feast_features import attach_feast_features
from src.features.preprocessing import RAW_INPUT_COLUMNS, model_input_columns
from src.models.inference import records_to_frame

log = get_logger(__name__)

PROB_FLOOR = 1e-6
EDUCATION_NAMES = {1: "graduate school", 2: "university", 3: "high school", 4: "other"}
FEATURE_LABELS = {
    "util_last": "Credit utilisation, latest month",
    "util_mean": "Credit utilisation, 6-month average",
    "util_max": "Credit utilisation, 6-month peak",
    "pay_ratio_mean": "Share of bill paid, 6-month average",
    "pay_ratio_last": "Share of bill paid, latest month",
    "pay_delay_max": "Worst payment delay (months)",
    "pay_delay_mean": "Average payment delay (months)",
    "months_delayed": "Months with a delayed payment",
    "bill_change_ratio": "Change in bill, latest vs earlier",
    "LIMIT_BAL": "Credit limit",
    "EDUCATION": "Education level",
}


def feature_label(feature: str) -> str:
    """Readable name for a model feature (engineered, raw or one-hot)."""
    if feature in FEATURE_LABELS:
        return FEATURE_LABELS[feature]
    if feature.startswith("education_grouped_"):
        code = int(float(feature.rsplit("_", 1)[1]))
        return f"Education: {EDUCATION_NAMES.get(code, code)}"
    for prefix, name in (
        ("PAY_AMT", "Payment amount, month "),
        ("BILL_AMT", "Bill amount, month "),
    ):
        if feature.startswith(prefix):
            return name + feature[len(prefix) :]
    if feature.startswith("PAY_"):
        return "Repayment status, month " + feature[4:]
    return feature


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, PROB_FLOOR, 1 - PROB_FLOOR)
    return np.log(p / (1 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-z))


def transform(
    model: Any, frame: pd.DataFrame, columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """Raw fields -> the model's feature space (everything before the final estimator)."""
    columns = list(columns or RAW_INPUT_COLUMNS)
    return model[:-1].transform(frame[columns])


def make_background(
    model: Any, train: pd.DataFrame, params: dict[str, Any], columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """Seeded sample of training rows in the model's feature space."""
    n = min(params["explain"]["background_size"], len(train))
    rng = make_rng(params["seed"], "explain:background")
    sample = train.iloc[np.sort(rng.choice(len(train), size=n, replace=False))]
    return transform(model, sample, columns).reset_index(drop=True)


def explain_matrix(
    model: Any, background: pd.DataFrame, features: pd.DataFrame, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """(contributions, base_value) in log-odds for rows already in model feature space.
    contributions has shape (n_rows, n_features); base_value has shape (n_rows,)."""
    estimator = model[-1]
    columns = list(background.columns)
    if list(features.columns) != columns:
        raise ConfigError("features and background must have the same columns")
    if isinstance(estimator, LogisticRegression):
        mean = background.to_numpy(dtype=float).mean(axis=0)
        coefficients = estimator.coef_[0]
        contributions = (features.to_numpy(dtype=float) - mean) * coefficients
        base = float(estimator.intercept_[0] + coefficients @ mean)
        return contributions, np.full(len(features), base)

    import shap

    def predict(values: np.ndarray) -> np.ndarray:
        frame = pd.DataFrame(values, columns=columns)
        return _logit(estimator.predict_proba(frame)[:, 1])

    masker = shap.maskers.Independent(background.to_numpy(dtype=float), max_samples=len(background))
    explainer = shap.Explainer(predict, masker, algorithm="permutation", seed=seed)
    result = explainer(features.to_numpy(dtype=float), max_evals=2 * len(columns) + 1, silent=True)
    return np.asarray(result.values), np.asarray(result.base_values, dtype=float)


def explain_frame(
    model: Any,
    background: pd.DataFrame,
    raw: pd.DataFrame,
    top_k: int = 5,
    columns: Sequence[str] | None = None,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """One explanation per row of `raw` (raw API fields): probability, base value and the
    `top_k` factors by absolute contribution."""
    features = transform(model, raw, columns)
    contributions, base = explain_matrix(model, background, features, seed)
    results = []
    for i in range(len(features)):
        order = np.argsort(-np.abs(contributions[i]))[:top_k]
        log_odds = float(base[i] + contributions[i].sum())
        results.append(
            {
                "probability": float(_sigmoid(log_odds)),
                "log_odds": log_odds,
                "base_value": float(base[i]),
                "factors": [
                    {
                        "feature": features.columns[j],
                        "label": feature_label(features.columns[j]),
                        "value": float(features.iloc[i, j]),
                        "contribution": float(contributions[i, j]),
                        "direction": (
                            "increases risk" if contributions[i, j] > 0 else "decreases risk"
                        ),
                    }
                    for j in order
                ],
            }
        )
    return results


def explain_records(
    model: Any,
    background: pd.DataFrame,
    records: Sequence[Mapping[str, Any]],
    top_k: int = 5,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Serving helper: raw request fields (same validation as `inference.predict`) in,
    explanations out."""
    return explain_frame(model, background, records_to_frame(records), top_k, seed=seed)


def load_background(path: Any) -> pd.DataFrame:
    import pathlib

    if not pathlib.Path(path).is_file():
        raise IngestionError(f"{path} not found (run `python -m src.models.explain`)")
    return pd.read_parquet(path)


def global_importance(
    model: Any,
    background: pd.DataFrame,
    raw: pd.DataFrame,
    columns: Sequence[str] | None,
    seed: int,
) -> list[dict[str, Any]]:
    """Mean |contribution| per model feature over `raw`, largest first."""
    features = transform(model, raw, columns)
    contributions, _ = explain_matrix(model, background, features, seed)
    mean_abs = np.abs(contributions).mean(axis=0)
    order = np.argsort(-mean_abs)
    return [
        {
            "feature": features.columns[j],
            "label": feature_label(features.columns[j]),
            "mean_abs_contribution": float(mean_abs[j]),
        }
        for j in order
    ]


def importance_chart(importance: list[dict[str, Any]], top: int = 12) -> bytes:
    from io import BytesIO

    from matplotlib.figure import Figure

    rows = importance[:top][::-1]
    fig = Figure(figsize=(7, 4.5))
    ax = fig.subplots()
    ax.barh([r["label"] for r in rows], [r["mean_abs_contribution"] for r in rows])
    ax.set(xlabel="Mean |contribution| to log-odds of default", title="Global feature importance")
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100)
    return buffer.getvalue()


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    for needed in (model_path(params, smoke), metrics_path(params, smoke)):
        if not needed.is_file():
            raise IngestionError(f"{needed} not found (run train first)")
    model = joblib.load(model_path(params, smoke))  # trusted, locally produced artifact only
    columns = model_input_columns(params)
    frames = attach_feast_features(
        {n: pd.read_parquet(split_path(params, n, smoke)) for n in ("train", "test")}, params, smoke
    )

    background = make_background(model, frames["train"], params, columns)
    write_parquet(explainer_background_path(params, smoke), background)

    seed = derive_seed(params["seed"], "explain:shap")
    test = frames["test"]
    rng = make_rng(params["seed"], "explain:sample")
    sample = test.iloc[
        np.sort(
            rng.choice(
                len(test),
                size=min(params["explain"]["importance_sample"], len(test)),
                replace=False,
            )
        )
    ]
    importance = global_importance(model, background, sample[columns], columns, seed)

    probabilities = model.predict_proba(sample[columns])[:, 1]
    riskiest = sample.iloc[[int(np.argmax(probabilities))]]
    example = explain_frame(
        model, background, riskiest[columns], params["explain"]["top_k"], columns, seed
    )[0]
    estimator = model[-1]
    report = {
        "smoke": smoke,
        "algorithm": type(estimator).__name__,
        "method": (
            "exact linear contributions (coefficient x deviation from background mean)"
            if isinstance(estimator, LogisticRegression)
            else "SHAP permutation explainer (model agnostic, exactly additive)"
        ),
        "unit": (
            "log-odds of default; positive = higher risk; "
            "base_value + sum(contributions) = log-odds"
        ),
        "background_rows": int(len(background)),
        "importance_rows": int(len(sample)),
        "n_model_features": int(background.shape[1]),
        "global_importance": importance,
        "example_highest_risk_test_row": example,
        "caveats": [
            "Explains the model, not real-world causes.",
            "Sensitive attributes are not model inputs, so they never appear as factors.",
        ],
    }
    write_json(explanations_path(params, smoke), report)
    chart = explanations_path(params, smoke).with_suffix(".png")
    atomic_write_bytes(chart, importance_chart(importance))
    log.info("explanations: top factor %s", importance[0]["feature"])
    return {
        "top_feature": importance[0]["feature"],
        "background": str(explainer_background_path(params, smoke)),
    }


if __name__ == "__main__":
    raise SystemExit(
        stage_main(run, description="Explanations: SHAP background, importance, example")
    )
