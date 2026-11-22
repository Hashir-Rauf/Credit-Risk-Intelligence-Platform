"""Cycle 5 tests: contributions are exact, readable and never use sensitive attributes."""

from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest
from src.common.config import load_params
from src.common.errors import ConfigError, IngestionError
from src.data.paths import explainer_background_path, explanations_path, model_path
from src.features.preprocessing import RAW_INPUT_COLUMNS, build_pipeline
from src.models import explain
from src.models.explain import (
    explain_frame,
    explain_matrix,
    explain_records,
    feature_label,
    load_background,
    make_background,
    transform,
)
from src.models.train import build_estimator


def raw_frame(n=300, seed=0):
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            "LIMIT_BAL": rng.integers(10_000, 500_000, n),
            "EDUCATION": rng.integers(1, 5, n),
            **{f"PAY_{i}": rng.integers(-1, 4, n) for i in range(1, 7)},
            **{f"BILL_AMT{i}": rng.integers(0, 200_000, n) for i in range(1, 7)},
            **{f"PAY_AMT{i}": rng.integers(0, 50_000, n) for i in range(1, 7)},
        }
    )
    risk = frame["PAY_1"] + frame["PAY_2"] + rng.normal(0, 1, n)
    return frame[RAW_INPUT_COLUMNS], (risk > 2).astype(int)


def fitted(params, algorithm, **kw):
    X, y = raw_frame()
    model = build_pipeline(params, build_estimator(algorithm, kw, params["seed"])).fit(X, y)
    return model, X


@pytest.fixture(scope="module")
def models():
    params = load_params()
    out = {
        "logistic_regression": fitted(params, "logistic_regression"),
        "random_forest": fitted(params, "random_forest", n_estimators=15, max_depth=4),
        "hist_gradient_boosting": fitted(
            params, "hist_gradient_boosting", max_iter=20, max_depth=3
        ),
    }
    return out


def log_odds(model, X):
    p = np.clip(model.predict_proba(X)[:, 1], 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


@pytest.mark.parametrize(
    "algorithm", ["logistic_regression", "random_forest", "hist_gradient_boosting"]
)
def test_contributions_add_up_to_the_models_log_odds(models, params, algorithm):
    model, X = models[algorithm]
    background = make_background(model, X, params)
    rows = X.iloc[:4]
    contributions, base = explain_matrix(model, background, transform(model, rows))
    np.testing.assert_allclose(base + contributions.sum(axis=1), log_odds(model, rows), atol=1e-6)


def test_linear_contributions_match_a_brute_force_calculation(models, params):
    model, X = models["logistic_regression"]
    background = make_background(model, X, params)
    row = transform(model, X.iloc[[3]])
    contributions, _ = explain_matrix(model, background, row)
    coef = model[-1].coef_[0]
    expected = coef * (row.to_numpy()[0] - background.to_numpy().mean(axis=0))
    np.testing.assert_allclose(contributions[0], expected)


def test_an_average_background_row_has_zero_contribution(models, params):
    model, X = models["logistic_regression"]
    background = make_background(model, X, params)
    mean_row = pd.DataFrame([background.mean()], columns=background.columns)
    contributions, _ = explain_matrix(model, background, mean_row)
    np.testing.assert_allclose(contributions, 0, atol=1e-10)


def test_tree_explanations_are_seeded(models, params):
    model, X = models["random_forest"]
    background = make_background(model, X, params)
    features = transform(model, X.iloc[:2])
    a, _ = explain_matrix(model, background, features, seed=7)
    b, _ = explain_matrix(model, background, features, seed=7)
    np.testing.assert_array_equal(a, b)


def test_mismatched_columns_are_rejected(models, params):
    model, X = models["logistic_regression"]
    background = make_background(model, X, params)
    with pytest.raises(ConfigError, match="same columns"):
        explain_matrix(model, background, transform(model, X.iloc[:2]).iloc[:, :-1])


class TestExplainFrame:
    def test_top_factors_are_ordered_and_labelled(self, models, params):
        model, X = models["logistic_regression"]
        background = make_background(model, X, params)
        result = explain_frame(model, background, X.iloc[:3], top_k=4)
        assert len(result) == 3
        for item in result:
            sizes = [abs(f["contribution"]) for f in item["factors"]]
            assert len(sizes) == 4 and sizes == sorted(sizes, reverse=True)
            assert 0 < item["probability"] < 1
            for f in item["factors"]:
                assert f["label"] and f["direction"] == (
                    "increases risk" if f["contribution"] > 0 else "decreases risk"
                )

    def test_probability_matches_the_model(self, models, params):
        model, X = models["logistic_regression"]
        background = make_background(model, X, params)
        item = explain_frame(model, background, X.iloc[[0]])[0]
        assert item["probability"] == pytest.approx(
            model.predict_proba(X.iloc[[0]])[0, 1], abs=1e-5
        )

    def test_sensitive_attributes_can_never_be_factors(self, models, params):
        model, X = models["logistic_regression"]
        background = make_background(model, X, params)
        assert not {"SEX", "AGE", "MARRIAGE"} & set(background.columns)
        for item in explain_frame(model, background, X.iloc[:20], top_k=len(background.columns)):
            assert not {"SEX", "AGE", "MARRIAGE"} & {f["feature"] for f in item["factors"]}

    def test_a_clearly_late_customer_is_pushed_towards_risk(self, models, params):
        model, X = models["logistic_regression"]
        background = make_background(model, X, params)
        late = X.iloc[[0]].copy()
        late[[f"PAY_{i}" for i in range(1, 7)]] = 3
        factors = explain_frame(model, background, late, top_k=3)[0]["factors"]
        assert any(f["feature"].startswith("pay_delay") and f["contribution"] > 0 for f in factors)


class TestExplainRecords:
    def test_accepts_raw_api_fields_and_validates_them(self, models, params):
        model, X = models["logistic_regression"]
        background = make_background(model, X, params)
        record = {c: int(X.iloc[0][c]) for c in RAW_INPUT_COLUMNS}
        assert explain_records(model, background, [record])[0]["factors"]
        with pytest.raises(Exception, match="PAY_1|missing|required"):
            explain_records(model, background, [{k: v for k, v in record.items() if k != "PAY_1"}])


class TestLabels:
    @pytest.mark.parametrize(
        "feature, text",
        [
            ("pay_delay_max", "Worst payment delay"),
            ("util_last", "utilisation"),
            ("PAY_3", "Repayment status, month 3"),
            ("BILL_AMT2", "Bill amount, month 2"),
            ("PAY_AMT6", "Payment amount, month 6"),
            ("education_grouped_2.0", "university"),
            ("something_new", "something_new"),
        ],
    )
    def test_readable_names(self, feature, text):
        assert text in feature_label(feature)


def test_load_background_reports_a_missing_file(tmp_path):
    with pytest.raises(IngestionError, match="explain"):
        load_background(tmp_path / "nope.parquet")


class TestExplainStage:
    def test_outputs(self, pipeline_env):
        params = pipeline_env.params
        explain.run(params)
        report = json.loads(explanations_path(params).read_text())
        background = load_background(explainer_background_path(params))
        assert len(background) == min(
            params["explain"]["background_size"],
            pipeline_env.results["preprocess"]["splits"]["train"],
        )
        assert report["n_model_features"] == background.shape[1]
        importance = [g["mean_abs_contribution"] for g in report["global_importance"]]
        assert importance == sorted(importance, reverse=True) and importance[0] > 0
        example = report["example_highest_risk_test_row"]
        assert len(example["factors"]) == params["explain"]["top_k"]
        assert (
            abs(
                example["base_value"]
                + sum(f["contribution"] for f in example["factors"])
                - example["log_odds"]
            )
            < 5
        )
        assert (
            explanations_path(params).with_suffix(".png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        )

    def test_background_works_with_the_saved_model(self, pipeline_env):
        params = pipeline_env.params
        explain.run(params)
        model = joblib.load(model_path(params))
        background = load_background(explainer_background_path(params))
        X, _ = raw_frame(3)
        item = explain_records(model, background, X.to_dict("records"))[0]
        assert 0 < item["probability"] < 1

    def test_no_sensitive_feature_in_the_report(self, pipeline_env):
        explain.run(pipeline_env.params)
        text = explanations_path(pipeline_env.params).read_text()
        names = {g["feature"] for g in json.loads(text)["global_importance"]}
        assert not {"SEX", "AGE", "MARRIAGE"} & names

    def test_deterministic(self, pipeline_env):
        explain.run(pipeline_env.params)
        first = explanations_path(pipeline_env.params).read_bytes()
        explain.run(pipeline_env.params)
        assert first == explanations_path(pipeline_env.params).read_bytes()

    def test_requires_a_trained_model(self, iso_params):
        with pytest.raises(IngestionError, match="run train first"):
            explain.run(iso_params)

    def test_smoke_is_isolated(self, iso_params, tmp_path):
        from conftest import run_stages

        run_stages(iso_params, "evaluate", smoke=True)
        explain.run(iso_params, smoke=True)
        assert explanations_path(iso_params, True).is_relative_to(tmp_path / "smoke")
        assert not explanations_path(iso_params).exists()
