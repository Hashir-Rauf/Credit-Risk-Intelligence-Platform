"""Cycle 2 tests: feature engineering, splitting, pipeline composition, feature schema,
and the preprocess stage."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from src.common.cli import ExitCode, stage_main
from src.common.errors import DataValidationError, IngestionError
from src.common.io import sha256_file
from src.data.paths import feature_schema_path, split_path, validation_report_path
from src.data.schema import BILL_AMT_COLUMNS, PAY_AMT_COLUMNS, PAY_COLUMNS, canonical_columns
from src.data.synthetic import make_synthetic_credit_data
from src.features import preprocessing
from src.features.preprocessing import (
    ENGINEERED_CATEGORICAL,
    ENGINEERED_COLUMNS,
    ENGINEERED_NUMERIC,
    RAW_INPUT_COLUMNS,
    SENSITIVE_CATEGORICAL,
    build_feature_schema,
    build_pipeline,
    engineer_features,
    model_input_columns,
    split_data,
)

from conftest import make_data

SENSITIVE = ["SEX", "MARRIAGE", "AGE"]


def example_row(**overrides: Any) -> pd.DataFrame:
    row = {
        "LIMIT_BAL": 100000,
        "EDUCATION": 5,
        **dict(zip(PAY_COLUMNS, [2, 0, -1, -2, 1, 0], strict=True)),
        **dict(zip(BILL_AMT_COLUMNS, [50000, 40000, 0, 20000, 10000, 30000], strict=True)),
        **dict(zip(PAY_AMT_COLUMNS, [4000, 10000, 5000, 0, 3000, 100], strict=True)),
    }
    row.update(overrides)
    return pd.DataFrame([row])


# --------------------------------------------------------------------------- engineering


class TestEngineerFeatures:
    def test_hand_computed_example(self):
        out = engineer_features(example_row()).iloc[0]
        assert out["util_last"] == pytest.approx(0.5)
        assert out["util_mean"] == pytest.approx(0.25)
        assert out["util_max"] == pytest.approx(0.5)
        # payment k settles bill k+1: 4000/40000, nothing owed -> 1, 5000/20000, 0/10000, 3000/30000
        assert out["pay_ratio_mean"] == pytest.approx((0.1 + 1 + 0.25 + 0 + 0.1) / 5)
        assert out["pay_ratio_last"] == pytest.approx(0.1)
        assert out["pay_delay_max"] == 2
        assert out["pay_delay_mean"] == pytest.approx(0.0)
        assert out["months_delayed"] == 2
        assert out["bill_change_ratio"] == pytest.approx(0.2)
        assert out["education_grouped"] == 4  # undocumented code 5 -> "others"

    @pytest.mark.parametrize(
        ("code", "expected"), [(0, 4), (1, 1), (2, 2), (3, 3), (4, 4), (5, 4), (6, 4)]
    )
    def test_education_grouping(self, code, expected):
        assert (
            engineer_features(example_row(EDUCATION=code))["education_grouped"].iloc[0] == expected
        )

    def test_adds_exactly_the_engineered_columns_and_keeps_inputs(self, valid_df):
        out = engineer_features(valid_df)
        assert set(out.columns) - set(valid_df.columns) == set(ENGINEERED_COLUMNS)
        pd.testing.assert_frame_equal(out[valid_df.columns], valid_df)

    def test_does_not_mutate_its_input(self, valid_df):
        before = valid_df.copy()
        engineer_features(valid_df)
        pd.testing.assert_frame_equal(valid_df, before)

    def test_is_row_wise_so_batch_and_single_row_agree(self, valid_df):
        """Statelessness = no leakage and no training-serving skew."""
        batch = engineer_features(valid_df.head(25))
        for i in (0, 7, 24):
            single = engineer_features(valid_df.iloc[[i]])
            pd.testing.assert_frame_equal(
                single.reset_index(drop=True), batch.iloc[[i]].reset_index(drop=True)
            )

    def test_independent_of_other_rows_and_the_target(self, valid_df):
        a = engineer_features(valid_df.head(50))
        shuffled = valid_df.head(50).copy()
        shuffled["default"] = 1 - shuffled["default"]
        b = engineer_features(shuffled.iloc[::-1]).iloc[::-1]
        pd.testing.assert_frame_equal(a[ENGINEERED_COLUMNS], b[ENGINEERED_COLUMNS])

    def test_works_without_target_or_sensitive_columns(self):
        assert set(ENGINEERED_COLUMNS) <= set(engineer_features(example_row()).columns)

    def test_zero_limit_does_not_produce_inf_or_nan(self):
        out = engineer_features(example_row(LIMIT_BAL=0))
        assert np.isfinite(out[ENGINEERED_NUMERIC].to_numpy(dtype=float)).all()

    def test_pay_ratio_is_clipped_and_defaults_to_one_when_nothing_is_owed(self):
        bills = dict.fromkeys(BILL_AMT_COLUMNS, 0)
        out = engineer_features(example_row(**bills, PAY_AMT1=500, PAY_AMT2=0))
        assert out["pay_ratio_mean"].iloc[0] == pytest.approx(1.0)
        huge = engineer_features(example_row(BILL_AMT2=10, PAY_AMT1=10**6))
        assert huge["pay_ratio_last"].iloc[0] == pytest.approx(1.5)

    def test_accepts_int_and_float_inputs_identically(self, valid_df):
        as_float = valid_df.astype("float64")
        pd.testing.assert_frame_equal(
            engineer_features(valid_df)[ENGINEERED_NUMERIC].astype("float64"),
            engineer_features(as_float)[ENGINEERED_NUMERIC],
        )

    def test_no_nan_or_inf_on_realistic_data(self, valid_df):
        out = engineer_features(valid_df)
        assert np.isfinite(out[ENGINEERED_NUMERIC].to_numpy(dtype=float)).all()
        assert out[ENGINEERED_CATEGORICAL[0]].isin([1, 2, 3, 4]).all()

    def test_missing_column_is_a_clear_error(self):
        with pytest.raises(ValueError, match="missing input columns.*PAY_3"):
            engineer_features(example_row().drop(columns=["PAY_3"]))


# --------------------------------------------------------------------------- splitting


class TestSplit:
    def test_sizes_follow_params_and_cover_all_rows_without_overlap(self, iso_params, valid_df):
        parts = split_data(valid_df, iso_params)
        sizes = {k: len(v) for k, v in parts.items()}
        assert sum(sizes.values()) == len(valid_df)
        assert sizes["train"] == pytest.approx(0.70 * len(valid_df), abs=2)
        assert sizes["val"] == pytest.approx(0.15 * len(valid_df), abs=2)
        assert sizes["test"] == pytest.approx(0.15 * len(valid_df), abs=2)
        ids = [set(p["customer_id"]) for p in parts.values()]
        assert ids[0].isdisjoint(ids[1]) and ids[0].isdisjoint(ids[2]) and ids[1].isdisjoint(ids[2])
        assert set().union(*ids) == set(valid_df["customer_id"])

    def test_stratified(self, iso_params, valid_df):
        overall = valid_df["default"].mean()
        for name, part in split_data(valid_df, iso_params).items():
            assert part["default"].mean() == pytest.approx(overall, abs=0.01), name

    def test_reproducible_and_seed_dependent(self, iso_params, valid_df):
        a = split_data(valid_df, iso_params)
        b = split_data(valid_df, iso_params)
        assert all(a[k].equals(b[k]) for k in a)
        iso_params["seed"] = 43
        c = split_data(valid_df, iso_params)
        assert not a["test"].equals(c["test"])

    def test_row_order_is_preserved_within_each_split(self, iso_params, valid_df):
        for part in split_data(valid_df, iso_params).values():
            assert part.index.is_monotonic_increasing

    def test_split_fractions_are_read_from_params(self, iso_params, valid_df):
        iso_params["split"].update(train=0.5, val=0.25, test=0.25)
        parts = split_data(valid_df, iso_params)
        assert len(parts["train"]) == pytest.approx(0.5 * len(valid_df), abs=2)


# --------------------------------------------------------------------------- pipeline


@pytest.fixture()
def split_frames(iso_params, valid_df):
    parts = split_data(valid_df, iso_params)
    return parts["train"], parts["val"]


class TestPipeline:
    def test_model_inputs_exclude_sensitive_by_default(self, iso_params):
        columns = model_input_columns(iso_params)
        assert columns == RAW_INPUT_COLUMNS
        assert not set(SENSITIVE) & set(columns)

    def test_fitted_model_never_uses_sensitive_attributes(self, iso_params, split_frames):
        train, _ = split_frames
        pipe = build_pipeline(iso_params, LogisticRegression(max_iter=500))
        pipe.fit(train, train["default"])  # even if sensitive columns are passed in
        names = list(pipe.named_steps["prep"].get_feature_names_out())
        assert not any(s in n for s in SENSITIVE for n in names), names

    def test_sensitive_attributes_included_only_when_the_flag_is_off(
        self, iso_params, split_frames
    ):
        iso_params["features"]["exclude_sensitive_from_model"] = False
        columns = model_input_columns(iso_params)
        assert set(SENSITIVE) <= set(columns)
        train, _ = split_frames
        pipe = build_pipeline(iso_params, LogisticRegression(max_iter=500)).fit(
            train[columns], train["default"]
        )
        names = list(pipe.named_steps["prep"].get_feature_names_out())
        assert "AGE" in names and any(n.startswith("SEX_") for n in names)

    def test_scaler_is_fitted_on_train_only(self, iso_params, split_frames):
        train, val = split_frames
        cols = model_input_columns(iso_params)
        pipe = build_pipeline(iso_params, LogisticRegression(max_iter=500)).fit(
            train[cols], train["default"]
        )
        scaler = pipe.named_steps["prep"].named_transformers_["numeric"]
        train_mean = engineer_features(train[cols])["LIMIT_BAL"].mean()
        mixed_mean = engineer_features(pd.concat([train, val])[cols])["LIMIT_BAL"].mean()
        index = list(scaler.feature_names_in_).index("LIMIT_BAL")
        assert scaler.mean_[index] == pytest.approx(train_mean)
        assert scaler.mean_[index] != pytest.approx(mixed_mean)

    def test_changing_val_or_test_rows_cannot_change_the_fitted_model(
        self, iso_params, split_frames
    ):
        train, val = split_frames
        cols = model_input_columns(iso_params)

        def coefficients(_unused_val: pd.DataFrame) -> np.ndarray:
            pipe = build_pipeline(iso_params, LogisticRegression(max_iter=500, random_state=1))
            pipe.fit(train[cols], train["default"])
            return pipe.named_steps["model"].coef_

        tampered = val.copy()
        tampered["default"] = 1 - tampered["default"]
        tampered["LIMIT_BAL"] = tampered["LIMIT_BAL"] * 100
        np.testing.assert_array_equal(coefficients(val), coefficients(tampered))

    def test_pipeline_accepts_raw_api_fields_and_gives_probabilities(
        self, iso_params, split_frames
    ):
        train, val = split_frames
        cols = model_input_columns(iso_params)
        pipe = build_pipeline(iso_params, LogisticRegression(max_iter=500)).fit(
            train[cols], train["default"]
        )
        proba = pipe.predict_proba(val[cols])
        assert proba.shape == (len(val), 2)
        assert ((proba >= 0) & (proba <= 1)).all()
        np.testing.assert_allclose(proba.sum(axis=1), 1.0)

    def test_unseen_category_does_not_crash(self, iso_params, split_frames):
        iso_params["features"]["exclude_sensitive_from_model"] = False
        train, val = split_frames
        cols = model_input_columns(iso_params)
        pipe = build_pipeline(iso_params, LogisticRegression(max_iter=500)).fit(
            train[cols], train["default"]
        )
        odd = val[cols].head(3).copy()
        odd["SEX"] = 9
        assert pipe.predict_proba(odd).shape == (3, 2)

    def test_all_sensitive_names_are_known_categories(self):
        assert set(SENSITIVE_CATEGORICAL) <= set(SENSITIVE)


# --------------------------------------------------------------------------- feature schema


class TestFeatureSchema:
    def test_inputs_match_model_columns_and_validation_rules(self, iso_params):
        schema = build_feature_schema(iso_params)
        assert [i["name"] for i in schema["inputs"]] == RAW_INPUT_COLUMNS
        by_name = {i["name"]: i for i in schema["inputs"]}
        rules = iso_params["validation"]
        assert by_name["PAY_1"]["min"] == rules["ranges"]["PAY"][0]
        assert by_name["PAY_1"]["max"] == rules["ranges"]["PAY"][1]
        assert by_name["PAY_1"]["dtype"] == "integer"
        assert by_name["EDUCATION"]["allowed"] == rules["domains"]["EDUCATION"]
        assert by_name["BILL_AMT3"]["min"] == rules["ranges"]["BILL_AMT"][0]
        assert by_name["LIMIT_BAL"]["dtype"] == "number"

    def test_documents_what_is_excluded_and_computed_server_side(self, iso_params):
        schema = build_feature_schema(iso_params)
        assert schema["excluded_sensitive"] == SENSITIVE
        assert schema["engineered_server_side"] == ENGINEERED_COLUMNS
        assert schema["target"] == "default"
        assert schema["decision_threshold"] == iso_params["evaluate"]["decision_threshold"]
        assert not {"SEX", "MARRIAGE", "AGE", "customer_id"} & {i["name"] for i in schema["inputs"]}
        json.dumps(schema)

    def test_follows_params_changes(self, iso_params):
        iso_params["validation"]["ranges"]["PAY"] = [-2, 12]
        assert (
            next(i for i in build_feature_schema(iso_params)["inputs"] if i["name"] == "PAY_2")[
                "max"
            ]
            == 12
        )

    def test_includes_sensitive_inputs_when_flag_is_off(self, iso_params):
        iso_params["features"]["exclude_sensitive_from_model"] = False
        schema = build_feature_schema(iso_params)
        assert {"SEX", "MARRIAGE", "AGE"} <= {i["name"] for i in schema["inputs"]}
        assert schema["excluded_sensitive"] == []


# --------------------------------------------------------------------------- preprocess stage


@pytest.fixture()
def validated(iso_params):
    make_data(iso_params)
    return iso_params


class TestPreprocessStage:
    def test_writes_three_splits_and_the_schema(self, validated):
        result = preprocessing.run(validated)
        assert set(result["splits"]) == {"train", "val", "test"}
        for name in ("train", "val", "test"):
            assert split_path(validated, name).is_file()
        assert json.loads(feature_schema_path(validated).read_text())["schema_version"] == 1

    def test_processed_files_hold_raw_engineered_sensitive_and_target_columns(self, validated):
        preprocessing.run(validated)
        train = pd.read_parquet(split_path(validated, "train"))
        assert set(canonical_columns(validated)) <= set(train.columns)
        assert set(ENGINEERED_COLUMNS) <= set(train.columns)
        assert {"SEX", "MARRIAGE", "AGE"} <= set(train.columns)  # kept for fairness reporting only

    def test_processed_engineered_columns_equal_a_fresh_computation(self, validated):
        preprocessing.run(validated)
        train = pd.read_parquet(split_path(validated, "train"))
        recomputed = engineer_features(train[canonical_columns(validated)])
        pd.testing.assert_frame_equal(train[ENGINEERED_COLUMNS], recomputed[ENGINEERED_COLUMNS])

    def test_outputs_are_byte_identical_across_runs(self, validated):
        preprocessing.run(validated)
        first = {n: sha256_file(split_path(validated, n)) for n in ("train", "val", "test")}
        schema_first = feature_schema_path(validated).read_bytes()
        preprocessing.run(validated)
        assert first == {n: sha256_file(split_path(validated, n)) for n in ("train", "val", "test")}
        assert schema_first == feature_schema_path(validated).read_bytes()

    def test_refuses_to_run_without_a_validation_report(self, iso_params):
        make_data(iso_params)
        validation_report_path(iso_params).unlink()
        with pytest.raises(IngestionError, match="validation report not found"):
            preprocessing.run(iso_params)

    def test_refuses_data_whose_validation_failed(self, validated):
        report_path = validation_report_path(validated)
        report = json.loads(report_path.read_text())
        report["passed"] = False
        report_path.write_text(json.dumps(report))
        with pytest.raises(DataValidationError):
            preprocessing.run(validated)
        assert not split_path(validated, "train").exists()

    def test_cli_exit_codes(self, validated, write_params):
        argv = ["--params", str(write_params(validated))]
        assert stage_main(preprocessing.run, description="t", argv=argv) == 0
        report_path = validation_report_path(validated)
        report_path.write_text(json.dumps({"passed": False}))
        assert (
            stage_main(preprocessing.run, description="t", argv=argv) == ExitCode.VALIDATION_FAILED
        )

    def test_smoke_run_stays_in_the_smoke_directory(self, iso_params, tmp_path):
        make_data(iso_params, smoke=True)
        preprocessing.run(iso_params, smoke=True)
        assert split_path(iso_params, "train", smoke=True).is_relative_to(tmp_path / "smoke")
        assert not split_path(iso_params, "train").exists()
        assert not feature_schema_path(iso_params).exists()

    def test_data_checks_make_synthetic_extremes_safe(self, validated):
        """Smoke-sized and full-sized frames both survive the whole stage."""
        for rows in (600, 3000):
            make_data(validated, rows=rows)
            result = preprocessing.run(validated)
            assert sum(result["splits"].values()) == rows

    def test_synthetic_generator_shape_matches_what_preprocess_expects(self, params):
        assert list(make_synthetic_credit_data(10, 1).columns) == canonical_columns(params)
