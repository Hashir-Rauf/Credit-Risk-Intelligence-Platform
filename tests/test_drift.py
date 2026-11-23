"""Cycle 5 tests: PSI/KS, scenario changes and the drift-simulation stage."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import ks_2samp
from src.common.errors import ConfigError, IngestionError, PipelineError
from src.data.paths import drift_report_path, reference_path, simulated_batches_dir
from src.features.preprocessing import RAW_INPUT_COLUMNS
from src.models import drift
from src.models.drift import apply_changes, drift_report, ks_statistic, psi, sample_batch


class TestPsi:
    def test_identical_samples_give_zero(self):
        x = np.random.default_rng(0).normal(size=2000)
        assert psi(x, x) == pytest.approx(0.0, abs=1e-12)

    def test_grows_with_the_shift(self):
        rng = np.random.default_rng(0)
        ref = rng.normal(size=5000)
        small, big = psi(ref, rng.normal(0.2, 1, 5000)), psi(ref, rng.normal(1.5, 1, 5000))
        assert 0 < small < 0.1 < 0.25 < big

    def test_same_distribution_resample_is_stable(self):
        rng = np.random.default_rng(1)
        assert psi(rng.normal(size=5000), rng.normal(size=5000)) < 0.05

    def test_discrete_values_known_result(self):
        ref = [0] * 50 + [1] * 50
        cur = [0] * 80 + [1] * 20
        expected = (0.8 - 0.5) * np.log(0.8 / 0.5) + (0.2 - 0.5) * np.log(0.2 / 0.5)
        assert psi(ref, cur) == pytest.approx(expected)

    def test_unseen_category_counts_as_drift_and_stays_finite(self):
        value = psi([0, 1] * 50, [0, 1, 2] * 40)
        assert np.isfinite(value) and value > 0.2

    def test_values_beyond_the_reference_range_land_in_open_end_bins(self):
        ref = np.arange(1000, dtype=float)
        assert psi(ref, ref + 5000) > 1.0

    def test_empty_input_is_an_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            psi([], [1.0])

    def test_is_asymmetric_only_through_the_reference_bins(self):
        ref, cur = np.arange(100.0), np.arange(100.0) + 20
        assert psi(ref, cur) > 0 and psi(cur, ref) > 0


def test_ks_matches_scipy():
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=300), rng.normal(0.5, 1, 300)
    assert ks_statistic(a, b) == ks_2samp(a, b).statistic
    assert ks_statistic(a, a) == 0


class TestDriftReport:
    def frames(self, shift):
        rng = np.random.default_rng(0)
        ref = pd.DataFrame({c: rng.normal(size=2000) for c in "abcd"})
        cur = pd.DataFrame({c: rng.normal(shift.get(c, 0), 1, 2000) for c in "abcd"})
        return ref, cur

    def test_alert_needs_the_configured_share_of_drifted_features(self, params):
        ref, cur = self.frames({"a": 3})  # 1 of 4 = 0.25 < 0.3
        quiet = drift_report(ref, cur, list("abcd"), params)
        assert quiet["drifted_features"] == ["a"] and not quiet["alert"]
        ref, cur = self.frames({"a": 3, "b": 3})  # 2 of 4 = 0.5
        loud = drift_report(ref, cur, list("abcd"), params)
        assert loud["alert"] and loud["drifted_share"] == 0.5 and loud["max_psi"] > 0.2

    def test_no_drift_no_alert(self, params):
        ref, cur = self.frames({})
        assert not drift_report(ref, cur, list("abcd"), params)["alert"]


class TestApplyChanges:
    def frame(self):
        return pd.DataFrame({c: [1, 2, 3] for c in RAW_INPUT_COLUMNS}).assign(
            LIMIT_BAL=[10_000, 500_000, 900_000]
        )

    def test_multiply_and_add_are_integer_valued(self, params):
        out = apply_changes(
            self.frame(),
            [
                {"columns": ["LIMIT_BAL"], "op": "multiply", "value": 1.55},
                {"columns": ["PAY_1"], "op": "add", "value": 2},
            ],
            params,
        )
        assert out["LIMIT_BAL"].tolist()[0] == 15_500
        assert out["PAY_1"].tolist() == [3, 4, 5]
        assert all(str(t) == "int64" for t in out.dtypes[["LIMIT_BAL", "PAY_1"]])

    def test_results_are_clipped_to_the_validation_ranges(self, params):
        out = apply_changes(
            self.frame(),
            [
                {"columns": ["LIMIT_BAL"], "op": "multiply", "value": 100},
                {"columns": ["PAY_2"], "op": "add", "value": 50},
            ],
            params,
        )
        assert out["LIMIT_BAL"].max() == params["validation"]["ranges"]["LIMIT_BAL"][1]
        assert out["PAY_2"].max() == params["validation"]["ranges"]["PAY"][1]

    def test_input_frame_is_not_mutated(self, params):
        frame = self.frame()
        before = frame.copy()
        apply_changes(frame, [{"columns": ["LIMIT_BAL"], "op": "multiply", "value": 2}], params)
        pd.testing.assert_frame_equal(frame, before)

    def test_only_model_inputs_may_change(self, params):
        frame = self.frame().assign(SEX=1)
        with pytest.raises(ConfigError, match="not a model input"):
            apply_changes(frame, [{"columns": ["SEX"], "op": "add", "value": 1}], params)

    def test_no_changes_is_identity(self, params):
        frame = self.frame()
        pd.testing.assert_frame_equal(apply_changes(frame, [], params), frame)


class TestSampleBatch:
    def test_deterministic_and_named(self):
        test = pd.DataFrame({"x": np.arange(1000)})
        a, b = sample_batch(test, 100, 42, "s"), sample_batch(test, 100, 42, "s")
        pd.testing.assert_frame_equal(a, b)
        assert not a.equals(sample_batch(test, 100, 42, "other"))
        assert a["x"].is_unique

    def test_with_replacement_only_when_the_split_is_smaller(self):
        test = pd.DataFrame({"x": np.arange(10)})
        assert len(sample_batch(test, 50, 1, "s")) == 50
        assert sample_batch(test, 10, 1, "s")["x"].is_unique


class TestDriftStage:
    def test_outputs_and_scenarios_behave_as_designed(self, pipeline_env):
        params = pipeline_env.params
        drift.run(params)
        report = json.loads(drift_report_path(params).read_text())
        assert report["simulated"] is True and report["all_match_expectation"] is True
        names = [s["name"] for s in params["simulate"]["scenarios"]]
        assert sorted(report["scenarios"]) == sorted(names)
        for name, scenario in report["scenarios"].items():
            assert scenario["simulated"] and scenario["alert"] == scenario["expect_alert"]
            assert scenario["n_rows"] == params["simulate"]["batch_size"]
            assert (simulated_batches_dir(params) / f"{name}.parquet").is_file()
        assert not report["scenarios"]["no_drift"]["alert"]
        assert report["scenarios"]["no_drift"]["drifted_share"] == 0

    def test_reference_holds_inputs_score_and_label(self, pipeline_env):
        params = pipeline_env.params
        drift.run(params)
        reference = pd.read_parquet(reference_path(params))
        assert {"score", params["data"]["target_column"], *RAW_INPUT_COLUMNS} <= set(
            reference.columns
        )
        assert not {"SEX", "AGE", "MARRIAGE"} & set(reference.columns)
        assert len(reference) == pipeline_env.results["preprocess"]["splits"]["train"]
        assert reference["score"].between(0, 1).all()

    def test_shifted_batches_stay_valid_data(self, pipeline_env):
        params = pipeline_env.params
        drift.run(params)
        ranges = params["validation"]["ranges"]
        batch = pd.read_parquet(simulated_batches_dir(params) / "payment_delays.parquet")
        assert batch["PAY_1"].between(*ranges["PAY"]).all()
        assert batch["PAY_AMT1"].between(*ranges["PAY_AMT"]).all()

    def test_a_scenario_that_does_not_do_what_it_claims_fails_the_stage(self, pipeline_env):
        params = {**pipeline_env.params, "simulate": {**pipeline_env.params["simulate"]}}
        params["simulate"]["scenarios"] = [
            {
                "name": "claims_drift",
                "description": "no changes",
                "expect_alert": True,
                "changes": [],
            }
        ]
        with pytest.raises(PipelineError, match="claims_drift"):
            drift.run(params)
        saved = json.loads(drift_report_path(params).read_text())
        assert saved["all_match_expectation"] is False  # evidence is kept

    def test_report_is_byte_identical_across_runs(self, pipeline_env):
        drift.run(pipeline_env.params)
        first = drift_report_path(pipeline_env.params).read_bytes()
        drift.run(pipeline_env.params)
        assert first == drift_report_path(pipeline_env.params).read_bytes()

    def test_requires_a_trained_model(self, iso_params):
        with pytest.raises(IngestionError, match="run train first"):
            drift.run(iso_params)

    def test_smoke_is_isolated(self, iso_params, tmp_path):
        from conftest import run_stages

        run_stages(iso_params, "evaluate", smoke=True)
        drift.run(iso_params, smoke=True)
        assert reference_path(iso_params, True).is_relative_to(tmp_path / "smoke")
        assert (
            not reference_path(iso_params).exists() and not drift_report_path(iso_params).exists()
        )
