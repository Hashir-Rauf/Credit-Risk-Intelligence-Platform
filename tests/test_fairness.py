"""Cycle 5 tests: group fairness metrics, checks, the fairness stage and the ablation."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from src.common.cli import ExitCode, stage_main
from src.common.errors import ConfigError, GateFailedError, IngestionError
from src.data.paths import fairness_path, model_path
from src.models import fairness
from src.models.fairness import (
    age_band,
    attribute_report,
    disparities,
    fairness_regressions,
    fairness_report,
    group_metrics,
    run_checks,
)

# --------------------------------------------------------------------------- age bands


class TestAgeBand:
    EDGES = [30, 40, 50, 60]

    def test_labels_and_boundaries(self):
        out = age_band([21, 29, 30, 39, 40, 49, 50, 59, 60, 79], self.EDGES)
        assert out.tolist() == [
            "<30",
            "<30",
            "30-39",
            "30-39",
            "40-49",
            "40-49",
            "50-59",
            "50-59",
            "60+",
            "60+",
        ]

    def test_follows_the_configured_edges(self):
        assert age_band([25, 45], [35]).tolist() == ["<35", "35+"]


# --------------------------------------------------------------------------- group metrics


class TestGroupMetrics:
    def test_known_values(self):
        y = [1, 1, 0, 0, 0, 1]
        p = [0.9, 0.2, 0.8, 0.1, 0.3, 0.7]
        m = group_metrics(y, p, 0.5)
        # flagged (p>=0.5): rows 0, 2, 5 -> approval 3/6
        assert m["n"] == 6 and m["default_rate"] == pytest.approx(0.5)
        assert m["approval_rate"] == pytest.approx(0.5)
        assert m["tpr"] == pytest.approx(2 / 3)  # actual defaulters flagged: rows 0 and 5
        assert m["fpr"] == pytest.approx(1 / 3)  # non-defaulters flagged: row 2
        assert m["precision"] == pytest.approx(2 / 3)
        assert 0.5 < m["auc"] <= 1.0

    def test_undefined_values_are_none_not_errors(self):
        only_negatives = group_metrics([0, 0, 0], [0.1, 0.2, 0.9], 0.5)
        assert only_negatives["tpr"] is None and only_negatives["auc"] is None
        assert only_negatives["fpr"] == pytest.approx(1 / 3)
        nobody_flagged = group_metrics([1, 0], [0.1, 0.2], 0.5)
        assert nobody_flagged["precision"] is None and nobody_flagged["approval_rate"] == 1.0

    def test_threshold_is_inclusive(self):
        assert group_metrics([1, 0], [0.5, 0.2], 0.5)["tpr"] == 1.0


# --------------------------------------------------------------------------- disparities


def group(approval, tpr=0.5, fpr=0.2, auc=0.7):
    return {"approval_rate": approval, "tpr": tpr, "fpr": fpr, "auc": auc}


class TestDisparities:
    def test_gaps_and_impact_ratio(self):
        d = disparities(
            {"a": group(0.9, tpr=0.6), "b": group(0.6, tpr=0.4), "c": group(0.75, tpr=0.5)}
        )
        assert d["approval_rate_gap"] == pytest.approx(0.3)
        assert d["disparate_impact"] == pytest.approx(0.6 / 0.9)
        assert d["tpr_gap"] == pytest.approx(0.2) and d["n_groups"] == 3

    def test_a_single_group_has_no_gaps(self):
        d = disparities({"a": group(0.8)})
        assert (
            d["approval_rate_gap"] is None
            and d["disparate_impact"] is None
            and d["auc_gap"] is None
        )

    def test_missing_values_are_skipped_not_zero(self):
        d = disparities({"a": group(0.8, tpr=None), "b": group(0.7, tpr=0.4)})
        assert d["tpr_gap"] is None  # only one group has a TPR

    def test_identical_groups_have_zero_gap_and_impact_one(self):
        d = disparities({"a": group(0.8), "b": group(0.8)})
        assert d["approval_rate_gap"] == 0 and d["disparate_impact"] == 1.0


class TestAttributeReport:
    def test_small_groups_are_listed_but_excluded(self):
        labels = pd.Series(["x"] * 60 + ["y"] * 60 + ["z"] * 5)
        y = np.tile([0, 1], 63)[:125]
        p = np.linspace(0.05, 0.95, 125)
        report = attribute_report(labels, y, p, 0.5, min_size=50)
        assert sorted(report["groups"]) == ["x", "y"]
        assert report["excluded_small_groups"] == {"z": 5}
        assert report["disparities"]["n_groups"] == 2


class TestFairnessReport:
    def frame(self, n=400):
        rng = np.random.default_rng(0)
        sex = rng.integers(1, 3, n)
        return pd.DataFrame(
            {
                "SEX": sex,
                "MARRIAGE": rng.integers(1, 4, n),
                "AGE": rng.integers(21, 70, n),
                "default": rng.integers(0, 2, n),
            }
        )

    def test_covers_the_configured_attributes(self, iso_params):
        frame = self.frame()
        report = fairness_report(frame, np.random.default_rng(1).random(len(frame)), iso_params)
        assert list(report) == ["SEX", "AGE_BAND", "MARRIAGE"]
        assert set(report["SEX"]["groups"]) == {"1", "2"}
        assert set(report["AGE_BAND"]["groups"]) <= {"<30", "30-39", "40-49", "50-59", "60+"}

    def test_unknown_or_missing_attribute_columns_are_errors(self, iso_params):
        iso_params["fairness"]["attributes"] = ["EDUCATION"]
        with pytest.raises(ConfigError, match="column missing"):
            fairness_report(self.frame(), np.full(400, 0.5), iso_params)

    def test_a_model_that_scores_one_group_higher_is_flagged(self, iso_params):
        """Sanity check of the checks themselves: an obviously unfair scorer must fail them."""
        frame = self.frame(600)
        score = np.where(frame["SEX"] == 1, 0.9, 0.1)  # group 1 always declined, group 2 never
        report = fairness_report(frame, score, iso_params)
        assert report["SEX"]["groups"]["1"]["approval_rate"] == 0.0
        assert report["SEX"]["groups"]["2"]["approval_rate"] == 1.0
        failed = [c for c in run_checks(report, iso_params) if c["status"] == "failed"]
        assert {"SEX"} <= {c["attribute"] for c in failed}
        assert "disparate_impact" in {c["check"] for c in failed if c["attribute"] == "SEX"}


class TestRunChecks:
    def report(self, **gaps):
        base = {"disparate_impact": 0.95, "tpr_gap": 0.02, "auc_gap": 0.01}
        return {"SEX": {"disparities": {**base, **gaps}}}

    def status(self, report, params):
        return {c["check"]: c["status"] for c in run_checks(report, params)}

    def test_passing_failing_and_boundaries(self, params):
        assert set(self.status(self.report(), params).values()) == {"passed"}
        failing = self.status(self.report(disparate_impact=0.79, tpr_gap=0.2, auc_gap=0.06), params)
        assert set(failing.values()) == {"failed"}
        edge = self.status(self.report(disparate_impact=0.8, tpr_gap=0.10, auc_gap=0.05), params)
        assert set(edge.values()) == {"passed"}  # limits are inclusive

    def test_uncomputable_gaps_are_skipped_not_passed(self, params):
        status = self.status(self.report(disparate_impact=None, tpr_gap=None), params)
        assert status["disparate_impact"] == "skipped" and status["tpr_gap"] == "skipped"

    def test_limits_come_from_params(self, params):
        params["fairness"]["max_tpr_gap"] = 0.01
        assert self.status(self.report(), params)["tpr_gap"] == "failed"


class TestRegressions:
    def result(self, **gaps):
        base = dict.fromkeys(fairness.GAP_METRICS, 0.05)
        return {"SEX": {"disparities": {**base, **gaps}}}

    def test_only_gaps_that_grew_beyond_the_tolerance_count(self):
        assert fairness_regressions(self.result(tpr_gap=0.09), self.result(), 0.02) == [
            "SEX.tpr_gap"
        ]
        assert (
            fairness_regressions(self.result(tpr_gap=0.07), self.result(), 0.02) == []
        )  # at tolerance
        assert (
            fairness_regressions(self.result(tpr_gap=0.01), self.result(), 0.02) == []
        )  # improved

    def test_undefined_gaps_and_unknown_attributes_are_ignored(self):
        assert fairness_regressions(self.result(tpr_gap=None), self.result(), 0.0) == []
        other = {"MARRIAGE": {"disparities": dict.fromkeys(fairness.GAP_METRICS, 0.9)}}
        assert fairness_regressions(other, self.result(), 0.0) == []

    def test_every_gap_type_and_attribute_is_reported(self):
        worse = {"SEX": {"disparities": dict.fromkeys(fairness.GAP_METRICS, 0.5)}}
        assert fairness_regressions(worse, self.result(), 0.0) == [
            f"SEX.{g}" for g in fairness.GAP_METRICS
        ]


# --------------------------------------------------------------------------- stage


class TestFairnessStage:
    def test_report_contents(self, pipeline_env):
        fairness.run(pipeline_env.params)
        report = json.loads(fairness_path(pipeline_env.params).read_text())
        assert report["sensitive_attributes_in_model"] is False
        assert set(report["attributes"]) == {"SEX", "AGE_BAND", "MARRIAGE"}
        assert report["threshold"] == pipeline_env.params["evaluate"]["decision_threshold"]
        assert report["passed"] == (report["failed_checks"] == [])
        assert {c["status"] for c in report["checks"]} <= {"passed", "failed", "skipped"}
        assert report["enforced"] is False and report["notes"]
        sex = report["attributes"]["SEX"]["groups"]
        assert (
            sum(g["n"] for g in sex.values())
            == pipeline_env.results["preprocess"]["splits"]["test"]
        )

    def test_group_sizes_sum_to_the_test_split_for_every_attribute(self, pipeline_env):
        fairness.run(pipeline_env.params)
        report = json.loads(fairness_path(pipeline_env.params).read_text())
        n = pipeline_env.results["preprocess"]["splits"]["test"]
        for result in report["attributes"].values():
            kept = sum(g["n"] for g in result["groups"].values())
            assert kept + sum(result["excluded_small_groups"].values()) == n

    def test_ablation_documents_the_exclusion_decision(self, pipeline_env):
        fairness.run(pipeline_env.params)
        ablation = json.loads(fairness_path(pipeline_env.params).read_text())["ablation"]
        assert ablation["algorithm"] == "logistic_regression"
        assert set(ablation) >= {"with_sensitive_inputs", "without_sensitive_inputs", "conclusion"}
        gain = ablation["auc_gain_from_sensitive_inputs"]
        assert gain == pytest.approx(
            ablation["with_sensitive_inputs"]["test_roc_auc"]
            - ablation["without_sensitive_inputs"]["test_roc_auc"]
        )
        assert f"{gain:+.4f}" in ablation["conclusion"]

    def test_ablation_can_be_switched_off(self, pipeline_env):
        params = {
            **pipeline_env.params,
            "fairness": {**pipeline_env.params["fairness"], "run_ablation": False},
        }
        fairness.run(params)
        assert "ablation" not in json.loads(fairness_path(params).read_text())

    def test_report_is_byte_identical_across_runs(self, pipeline_env):
        fairness.run(pipeline_env.params)
        first = fairness_path(pipeline_env.params).read_bytes()
        fairness.run(pipeline_env.params)
        assert first == fairness_path(pipeline_env.params).read_bytes()

    def test_model_scores_do_not_depend_on_the_sensitive_columns(self, pipeline_env):
        """The report measures outcomes of a model that never sees SEX/AGE/MARRIAGE."""
        model = fairness.joblib.load(model_path(pipeline_env.params))
        test = fairness.load_test_frame(pipeline_env.params, False)
        columns = fairness.model_input_columns(pipeline_env.params)
        before = model.predict_proba(test[columns])[:, 1]
        flipped = test.copy()
        flipped["SEX"], flipped["AGE"] = 3 - flipped["SEX"], flipped["AGE"] + 10
        np.testing.assert_array_equal(before, model.predict_proba(flipped[columns])[:, 1])

    def test_failed_checks_only_fail_the_stage_when_enforced(self, pipeline_env, write_params):
        params = {
            **pipeline_env.params,
            "fairness": {
                **pipeline_env.params["fairness"],
                "max_tpr_gap": 0.0,
                "min_disparate_impact": 1.0,
            },
        }
        result = fairness.run(params)  # enforce is false: reported, not raised
        assert result["failed_checks"]
        params["fairness"]["enforce"] = True
        with pytest.raises(GateFailedError, match="fairness checks failed"):
            fairness.run(params)
        assert json.loads(fairness_path(params).read_text())["passed"] is False  # evidence is kept
        argv = ["--params", str(write_params(params))]
        assert stage_main(fairness.run, description="t", argv=argv) == ExitCode.GATE_FAILED

    def test_requires_a_trained_model(self, iso_params):
        with pytest.raises(IngestionError, match="run train first"):
            fairness.run(iso_params)

    def test_smoke_is_isolated(self, iso_params, tmp_path):
        from conftest import run_stages

        run_stages(iso_params, "evaluate", smoke=True)
        fairness.run(iso_params, smoke=True)
        assert fairness_path(iso_params, True).is_relative_to(tmp_path / "smoke")
        assert not fairness_path(iso_params).exists()


class TestAblationConclusion:
    def test_wording_follows_the_threshold(self, iso_params, monkeypatch):
        frame = pd.DataFrame(
            {
                "SEX": [1, 2] * 100,
                "MARRIAGE": [1, 2] * 100,
                "AGE": [25, 45] * 100,
                "default": [0, 1] * 100,
            }
        )
        for col in fairness.model_input_columns(iso_params):
            frame[col] = np.arange(200) % 7
        monkeypatch.setattr(
            fairness,
            "resolve_model_choice",
            lambda p, s: ("logistic_regression", {"max_iter": 50}, False),
        )
        monkeypatch.setattr(fairness, "build_pipeline", lambda p, e: _StubPipeline(p))
        out = fairness.ablation(iso_params, False, {"train": frame, "test": frame})
        assert "below the" in out["conclusion"] or "above the" in out["conclusion"]


class _StubPipeline:
    """Scores with AGE only when the variant includes sensitive inputs (so the gain is positive)."""

    def __init__(self, params):
        self.uses_age = not params["features"]["exclude_sensitive_from_model"]

    def fit(self, X, y):
        return self

    def predict_proba(self, X):
        p = (
            (X["AGE"].to_numpy() > 30).astype(float) * 0.8 + 0.1
            if self.uses_age
            else np.full(len(X), 0.5) + np.arange(len(X)) % 3 * 1e-3
        )
        return np.column_stack([1 - p, p])
