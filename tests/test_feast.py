"""Cycle 4 tests: Feast feature store (definitions, simulated timestamps, offline/online
retrieval, point-in-time correctness, quality checks, training from the store)."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml
from src.common.cli import ExitCode, stage_main
from src.common.config import load_params, validate_params
from src.common.errors import ConfigError, DataValidationError, FeatureNotFoundError, IngestionError
from src.data.paths import (
    feast_online_store_path,
    feast_registry_path,
    feast_report_path,
    feast_source_path,
    metrics_path,
    split_path,
)
from src.features import feast_features as ff
from src.features.feast_features import (
    FEATURE_GROUPS,
    SENSITIVE_COLUMNS,
    TIMESTAMP_FIELD,
    attach_feast_features,
    build_source_frames,
    get_online_features,
    get_training_features,
    simulated_timestamps,
    simulated_window,
)
from src.features.preprocessing import RAW_INPUT_COLUMNS
from src.models import inference, tracking

from conftest import isolate_params, run_stages, use_local_mlflow

KEY = "customer_id"


@pytest.fixture(scope="module")
def feast_env(tmp_path_factory):
    """Pipeline up to and including the feast stage on synthetic data (read-only for tests)."""
    base = tmp_path_factory.mktemp("feast")
    with pytest.MonkeyPatch.context() as patch:
        use_local_mlflow(patch, base)
        params = isolate_params(copy.deepcopy(load_params()), base)
        params["features"]["use_feast_for_training"] = True
        results = run_stages(params, "feast")
        raw = pd.read_csv(Path(params["data"]["raw_path"]))
        yield SimpleNamespace(params=params, base=base, results=results, raw=raw)


# --------------------------------------------------------------------------- definitions


class TestDefinitions:
    def test_feature_views_cover_exactly_the_model_inputs(self):
        columns = [c for cols in FEATURE_GROUPS.values() for c in cols]
        assert sorted(columns) == sorted(RAW_INPUT_COLUMNS)
        assert len(columns) == len(set(columns))

    def test_no_sensitive_column_in_any_view(self):
        assert not {c for cols in FEATURE_GROUPS.values() for c in cols} & set(SENSITIVE_COLUMNS)

    def test_definitions_have_entity_views_ttl_and_simulated_tag(self, params):
        entity, *views = ff.build_definitions(params)
        assert entity.join_key == KEY
        assert [v.name for v in views] == list(FEATURE_GROUPS)
        for view in views:
            assert view.ttl == timedelta(days=params["feast"]["ttl_days"])
            assert view.tags["timestamps"] == "simulated"
            assert sorted(f.name for f in view.schema) == sorted(FEATURE_GROUPS[view.name])

    def test_cli_yaml_matches_the_python_config(self, params, root):
        cfg = yaml.safe_load((root / "feature_repo" / "feature_store.yaml").read_text())
        built = ff.repo_config(params)
        assert cfg["project"] == built.project == params["feast"]["project"]
        assert cfg["provider"] == built.provider
        assert cfg["online_store"]["type"] == "sqlite"
        assert cfg["offline_store"]["type"] == "dask"
        assert cfg["entity_key_serialization_version"] == built.entity_key_serialization_version
        assert Path(cfg["registry"]).name == feast_registry_path(params).name
        assert Path(cfg["online_store"]["path"]).name == feast_online_store_path(params).name

    def test_feature_repo_module_exposes_the_same_objects(self, root, monkeypatch):
        monkeypatch.syspath_prepend(str(root / "feature_repo"))
        import importlib

        module = importlib.import_module("feature_definitions")
        assert module.entity.join_key == KEY
        assert {module.payment_history.name, module.billing.name, module.account.name} == set(
            FEATURE_GROUPS
        )


# --------------------------------------------------------------------------- timestamps


class TestSimulatedTimestamps:
    def test_inside_the_window_and_utc(self, params):
        stamps = simulated_timestamps(np.arange(1, 5001), params)
        start, end = simulated_window(params)
        assert str(stamps.dt.tz) == "UTC"
        assert stamps.min() >= pd.Timestamp(start) and stamps.max() < pd.Timestamp(end)

    def test_spread_over_the_whole_window(self, params):
        stamps = simulated_timestamps(np.arange(1, 5001), params)
        span = (stamps.max() - stamps.min()).total_seconds()
        assert span > 0.95 * params["feast"]["simulated_days"] * 86400
        assert stamps.nunique() > 4900

    def test_pure_function_of_seed_and_id(self, params):
        everyone = simulated_timestamps(np.arange(1, 1001), params)
        subset = simulated_timestamps(np.array([500, 7, 999]), params)
        assert subset.tolist() == [everyone[499], everyone[6], everyone[998]]
        assert simulated_timestamps(np.arange(1, 1001), params).equals(everyone)

    def test_seed_changes_the_assignment(self, params):
        a = simulated_timestamps(np.arange(1, 200), params)
        params["seed"] = 43
        assert not a.equals(simulated_timestamps(np.arange(1, 200), params))

    def test_window_params_are_respected(self, params):
        params["feast"].update(simulated_start="2030-03-01", simulated_days=10)
        stamps = simulated_timestamps(np.arange(1, 300), params)
        assert stamps.min() >= pd.Timestamp("2030-03-01", tz="UTC")
        assert stamps.max() < pd.Timestamp("2030-03-11", tz="UTC")

    def test_negative_ids_rejected(self, params):
        with pytest.raises(ConfigError, match="non-negative"):
            simulated_timestamps(np.array([-1, 2]), params)


# --------------------------------------------------------------------------- source frames


class TestSourceFrames:
    def test_one_frame_per_view_with_key_timestamp_and_int64_features(self, iso_params, valid_df):
        frames = build_source_frames(valid_df, iso_params)
        assert list(frames) == list(FEATURE_GROUPS)
        for view, frame in frames.items():
            assert list(frame.columns) == [KEY, TIMESTAMP_FIELD, *FEATURE_GROUPS[view]]
            assert frame[KEY].is_monotonic_increasing and frame[KEY].is_unique
            assert all(str(frame[c].dtype) == "int64" for c in FEATURE_GROUPS[view])

    def test_values_equal_the_raw_data(self, iso_params, valid_df):
        frames = build_source_frames(valid_df, iso_params)
        raw = valid_df.set_index("customer_id")
        for view, frame in frames.items():
            pd.testing.assert_frame_equal(
                frame.set_index(KEY)[FEATURE_GROUPS[view]],
                raw.loc[frame[KEY], FEATURE_GROUPS[view]].astype("int64"),
                check_names=False,
            )

    def test_sensitive_columns_never_reach_the_sources(self, iso_params, valid_df):
        for frame in build_source_frames(valid_df, iso_params).values():
            assert not set(SENSITIVE_COLUMNS) & set(frame.columns)

    def test_duplicate_ids_and_missing_columns_are_errors(self, iso_params, valid_df):
        with pytest.raises(DataValidationError, match="unique"):
            build_source_frames(pd.concat([valid_df, valid_df.head(1)]), iso_params)
        with pytest.raises(IngestionError, match="missing columns"):
            build_source_frames(valid_df.drop(columns=["PAY_3"]), iso_params)


# --------------------------------------------------------------------------- config


class TestFeastConfig:
    @pytest.mark.parametrize(
        ("mutate", "msg"),
        [
            (lambda p: p["feast"].update(simulated_start="soon"), "simulated_start"),
            (lambda p: p["feast"].update(simulated_days=0), "simulated_days"),
            (lambda p: p["feast"].update(parity_sample=0), "parity_sample"),
            (lambda p: p["feast"].update(label_lag_days=-1), "label_lag_days"),
            (lambda p: p["feast"].update(ttl_days=10, label_lag_days=30), "ttl_days"),
            (lambda p: p["feast"].update(max_null_fraction=2), "max_null_fraction"),
            (lambda p: p["feast"].update(entity_join_key=" "), "entity_join_key"),
            (lambda p: p["feast"].pop("report_path"), "report_path"),
            (
                lambda p: p["features"].update(use_feast_for_training="yes"),
                "use_feast_for_training",
            ),
            (
                lambda p: p["features"].update(exclude_sensitive_from_model=False),
                "exclude_sensitive_from_model",
            ),
        ],
    )
    def test_rejects_bad_values(self, params, mutate, msg):
        mutate(params)
        with pytest.raises(ConfigError, match=msg):
            validate_params(params)

    def test_flag_off_allows_sensitive_inputs(self, params):
        params["features"].update(use_feast_for_training=False, exclude_sensitive_from_model=False)
        validate_params(params)


# --------------------------------------------------------------------------- stage


class TestFeastStage:
    def test_all_quality_checks_pass_and_are_reported(self, feast_env):
        report = json.loads(feast_report_path(feast_env.params).read_text())
        assert report["status"] == "passed"
        names = {c["name"] for c in report["checks"]}
        assert names == {
            "no_sensitive_features",
            "views_cover_model_inputs",
            "same_rows_in_every_view",
            "unique_customers",
            "null_fraction",
            "timestamps_in_simulated_window",
            "online_store_materialised_and_matches_source",
            "offline_retrieval_matches_source",
            "point_in_time_no_future_leak",
        }
        assert all(c["passed"] for c in report["checks"])
        assert report["n_customers"] == len(feast_env.raw)
        assert "simulated" in report["timestamps"]["kind"]
        assert report["excluded_sensitive"] == list(SENSITIVE_COLUMNS)

    def test_outputs_exist(self, feast_env):
        p = feast_env.params
        for view in FEATURE_GROUPS:
            assert feast_source_path(p, view).is_file()
        assert feast_registry_path(p).is_file() and feast_online_store_path(p).is_file()

    def test_source_files_hold_no_sensitive_columns(self, feast_env):
        for view in FEATURE_GROUPS:
            columns = set(pd.read_parquet(feast_source_path(feast_env.params, view)).columns)
            assert (
                not columns & set(SENSITIVE_COLUMNS) and "customer_id" in columns
            )  # the entity key

    def test_close_store_releases_the_sqlite_connection(self, feast_env):
        """Windows cannot replace an online store file that a connection still holds open."""
        store = ff.get_store(feast_env.params)
        ff.get_online_features(feast_env.params, [1], store=store)
        online = store._get_provider().online_store
        assert online._conn is not None
        ff.close_store(store)
        assert online._conn is None

    def test_registry_lists_the_views_and_entity(self, feast_env):
        store = ff.get_store(feast_env.params)
        assert {v.name for v in store.list_feature_views()} == set(FEATURE_GROUPS)
        assert [e.join_key for e in store.list_entities()] == [KEY]

    def test_report_and_sources_are_byte_identical_across_runs(self, iso_params):
        iso_params["features"]["use_feast_for_training"] = True
        run_stages(iso_params, "feast")
        first = (
            feast_report_path(iso_params).read_bytes(),
            *[feast_source_path(iso_params, v).read_bytes() for v in FEATURE_GROUPS],
        )
        ff.run(iso_params)
        assert first == (
            feast_report_path(iso_params).read_bytes(),
            *[feast_source_path(iso_params, v).read_bytes() for v in FEATURE_GROUPS],
        )

    def test_refuses_unvalidated_data(self, iso_params):
        from src.data.paths import validation_report_path

        from conftest import make_data

        make_data(iso_params)
        validation_report_path(iso_params).unlink()
        with pytest.raises(IngestionError, match="validation report not found"):
            ff.run(iso_params)

    def test_failed_check_writes_the_report_then_exits_2(
        self, iso_params, write_params, monkeypatch
    ):
        run_stages(iso_params, "preprocess")
        real = ff.get_online_features

        def corrupted(*args, **kwargs):
            frame = real(*args, **kwargs)
            frame.loc[0, "LIMIT_BAL"] += 1  # online store "disagrees" with the source
            return frame

        monkeypatch.setattr(ff, "get_online_features", corrupted)
        argv = ["--params", str(write_params(iso_params))]
        assert stage_main(ff.run, description="t", argv=argv) == ExitCode.VALIDATION_FAILED
        report = json.loads(feast_report_path(iso_params).read_text())
        failed = [c["name"] for c in report["checks"] if not c["passed"]]
        assert report["status"] == "failed" and failed == [
            "online_store_materialised_and_matches_source"
        ]

    def test_smoke_run_is_isolated(self, iso_params, tmp_path):
        run_stages(iso_params, "feast", smoke=True)
        assert feast_registry_path(iso_params, True).is_relative_to(tmp_path / "smoke")
        assert feast_report_path(iso_params, True).is_file()
        assert not feast_registry_path(iso_params).exists()
        assert not feast_report_path(iso_params).exists()


# --------------------------------------------------------------------------- retrieval


class TestRetrieval:
    def test_training_retrieval_equals_the_preprocessed_splits(self, feast_env):
        """The key skew guard: what Feast serves for training is exactly what preprocess wrote."""
        p = feast_env.params
        for name in ("train", "val", "test"):
            split = pd.read_parquet(split_path(p, name))
            got = get_training_features(p, split["customer_id"].to_numpy())
            assert got[KEY].tolist() == split["customer_id"].tolist()
            pd.testing.assert_frame_equal(
                got[RAW_INPUT_COLUMNS].reset_index(drop=True),
                split[RAW_INPUT_COLUMNS].astype("int64").reset_index(drop=True),
            )

    def test_order_of_ids_is_preserved(self, feast_env):
        ids = np.array([900, 5, 301, 77])
        assert get_training_features(feast_env.params, ids)[KEY].tolist() == ids.tolist()
        assert get_online_features(feast_env.params, ids)[KEY].tolist() == ids.tolist()

    def test_online_equals_offline(self, feast_env):
        ids = np.arange(1, 120)
        online = get_online_features(feast_env.params, ids)
        offline = get_training_features(feast_env.params, ids)
        pd.testing.assert_frame_equal(online, offline)
        assert all(str(online[c].dtype) == "int64" for c in RAW_INPUT_COLUMNS)

    def test_unknown_customer_is_reported_not_nan(self, feast_env):
        for fetch in (get_online_features, get_training_features):
            with pytest.raises(FeatureNotFoundError) as caught:
                fetch(feast_env.params, np.array([1, 10**9]))
            assert caught.value.missing == [10**9]

    def test_empty_online_request(self, feast_env):
        with pytest.raises(ValueError, match="no customer ids"):
            get_online_features(feast_env.params, np.array([], dtype=int))

    def test_point_in_time_a_lookup_before_the_snapshot_gets_nothing(self, feast_env):
        p = feast_env.params
        ids = np.arange(1, 30)
        store = ff.get_store(p)
        early = ff.training_entity_frame(ids, p)
        early[TIMESTAMP_FIELD] = simulated_timestamps(ids, p) - timedelta(seconds=1)
        got = store.get_historical_features(entity_df=early, features=ff.feature_refs()).to_df()
        assert len(got) == 0 or got[RAW_INPUT_COLUMNS].isna().all().all()

    def test_lookup_exactly_at_the_snapshot_is_included(self, feast_env):
        p = copy.deepcopy(feast_env.params)
        p["feast"]["label_lag_days"] = 0
        got = get_training_features(p, np.arange(1, 30))
        assert len(got) == 29

    def test_lookup_after_ttl_returns_nothing(self, feast_env):
        p = copy.deepcopy(feast_env.params)
        p["feast"]["label_lag_days"] = (
            p["feast"]["ttl_days"] + 5
        )  # (validate_params not applied on purpose)
        with pytest.raises(FeatureNotFoundError):
            get_training_features(p, np.arange(1, 10))


class TestAttachFeatures:
    def frames(self, p):
        return {n: pd.read_parquet(split_path(p, n)) for n in ("train", "val", "test")}

    def test_noop_when_the_flag_is_off(self, feast_env):
        p = copy.deepcopy(feast_env.params)
        p["features"]["use_feast_for_training"] = False
        frames = self.frames(p)
        assert attach_feast_features(frames, p) is frames

    def test_returns_the_same_frames_when_store_and_parquet_agree(self, feast_env):
        frames = self.frames(feast_env.params)
        out = attach_feast_features(frames, feast_env.params)
        for name in frames:
            pd.testing.assert_frame_equal(
                out[name], frames[name].astype(out[name].dtypes.to_dict())
            )

    def test_features_really_come_from_the_store(self, feast_env):
        """Tamper with the parquet split: the store's values win and the target stays."""
        frames = self.frames(feast_env.params)
        tampered = {k: v.copy() for k, v in frames.items()}
        tampered["test"]["LIMIT_BAL"] = 1
        out = attach_feast_features(tampered, feast_env.params)
        assert out["test"]["LIMIT_BAL"].tolist() == frames["test"]["LIMIT_BAL"].tolist()
        target = feast_env.params["data"]["target_column"]
        assert out["test"][target].tolist() == frames["test"][target].tolist()

    def test_other_columns_and_order_are_untouched(self, feast_env):
        frames = self.frames(feast_env.params)
        out = attach_feast_features(frames, feast_env.params)
        for name, frame in frames.items():
            assert list(out[name].columns) == list(frame.columns)
            assert out[name]["customer_id"].tolist() == frame["customer_id"].tolist()
            assert out[name][["SEX", "AGE", "MARRIAGE"]].equals(frame[["SEX", "AGE", "MARRIAGE"]])


# --------------------------------------------------------------------- training from the store


class TestTrainingFromFeast:
    def test_metrics_are_identical_to_training_from_parquet(self, tmp_path):
        """End to end: the same pipeline fed by Feast gives bit-identical test metrics."""
        results = {}
        for use_feast in (False, True):
            with pytest.MonkeyPatch.context() as patch:
                base = tmp_path / f"feast_{use_feast}"
                use_local_mlflow(patch, base)
                params = isolate_params(copy.deepcopy(load_params()), base)
                params["features"]["use_feast_for_training"] = use_feast
                run_stages(params, "evaluate")
                results[use_feast] = json.loads(metrics_path(params).read_text())
                if use_feast:
                    run_id = json.loads((base / "reports" / "last_run.json").read_text())["run_id"]
                    from mlflow.tracking import MlflowClient

                    tags = (
                        MlflowClient(tracking_uri=tracking.tracking_uri(params))
                        .get_run(run_id)
                        .data.tags
                    )
                    assert tags["features_from_feast"] == "true"
                    assert tags["feast_repo_commit"] and "n/a" not in tags["feast_repo_commit"]
        assert results[True]["test"] == results[False]["test"]
        assert results[True]["val"] == results[False]["val"]

    def test_inference_by_customer_id_matches_scoring_the_raw_row(self, feast_env, monkeypatch):
        from src.models import train

        use_local_mlflow(monkeypatch, feast_env.base / "scoring")
        p = copy.deepcopy(feast_env.params)
        p["train"].update(
            model_path=str(feast_env.base / "scoring" / "model.joblib"),
            metrics_path=str(feast_env.base / "scoring" / "metrics.json"),
            plots_dir=str(feast_env.base / "scoring" / "plots"),
            last_run_path=str(feast_env.base / "scoring" / "last_run.json"),
        )
        train.run(p)
        model = inference.load_model(p["train"]["model_path"])
        ids = [3, 14, 159]
        by_id = inference.predict_for_customers(model, p, ids, 0.5)
        rows = feast_env.raw.set_index("customer_id").loc[ids, RAW_INPUT_COLUMNS].to_dict("records")
        direct = inference.predict(model, rows, 0.5)
        assert [r[KEY] for r in by_id] == ids
        for a, b in zip(by_id, direct, strict=True):
            assert (
                a["probability"] == pytest.approx(b["probability"], abs=1e-12)
                and a["prediction"] == b["prediction"]
            )
        with pytest.raises(FeatureNotFoundError):
            inference.predict_for_customers(model, p, [3, 10**9], 0.5)


# --------------------------------------------------------------------------- repo version tag


class TestFeastRepoCommit:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FEAST_REPO_COMMIT", "abc")
        assert tracking.feast_repo_commit() == "abc"

    def test_git_commit_and_dirty_flag(self, monkeypatch):
        monkeypatch.delenv("FEAST_REPO_COMMIT", raising=False)
        answers = {"log": "deadbeef", "status": ""}
        monkeypatch.setattr(tracking, "_git", lambda *a: answers[a[0]])
        assert tracking.feast_repo_commit() == "deadbeef"
        answers["status"] = " M feature_repo/feature_store.yaml"
        assert tracking.feast_repo_commit() == "deadbeef+dirty"

    def test_hash_fallback_tracks_file_content(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FEAST_REPO_COMMIT", raising=False)
        monkeypatch.setattr(tracking, "_git", lambda *a: None)
        monkeypatch.setattr(tracking, "repo_root", lambda: tmp_path)
        (tmp_path / "feature_repo").mkdir()
        (tmp_path / "feature_repo" / "a.yaml").write_text("x: 1\n")
        (tmp_path / "src" / "features").mkdir(parents=True)
        (tmp_path / "src" / "features" / "feast_features.py").write_text("A = 1\n")
        first = tracking.feast_repo_commit()
        assert first.startswith("sha256:") and tracking.feast_repo_commit() == first
        (tmp_path / "feature_repo" / "a.yaml").write_text("x: 2\n")
        assert tracking.feast_repo_commit() != first
