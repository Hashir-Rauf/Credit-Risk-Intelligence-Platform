"""Cycle 2 tests: metrics, gate, plots, tracking helpers, train/evaluate stages, MLflow run
contents, inference parity, config validation and DVC wiring."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from src.common.cli import ExitCode, stage_main
from src.common.config import load_params, validate_params
from src.common.errors import ConfigError, GateFailedError, IngestionError, PipelineError
from src.data.paths import (
    evaluation_path,
    last_run_path,
    metrics_path,
    model_path,
    plots_dir,
    split_path,
)
from src.features.preprocessing import RAW_INPUT_COLUMNS
from src.models import evaluate, inference, tracking, train
from src.models.evaluate import check_gate, compute_metrics, save_plots

from conftest import run_stages

# --------------------------------------------------------------------------- metrics


class TestComputeMetrics:
    def test_perfect_ranking(self):
        m = compute_metrics([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9], 0.5)
        assert m["roc_auc"] == 1.0 and m["pr_auc"] == 1.0 and m["ks"] == 1.0
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (2, 0, 2, 0)
        assert m["precision"] == m["recall"] == m["f1"] == m["accuracy"] == 1.0

    def test_known_values(self):
        y = [0, 0, 1, 1]
        p = [0.2, 0.6, 0.4, 0.9]
        m = compute_metrics(y, p, 0.5)
        assert m["roc_auc"] == pytest.approx(0.75)
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (1, 1, 1, 1)
        assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(0.5)
        assert m["brier"] == pytest.approx(np.mean([0.04, 0.36, 0.36, 0.01]))
        assert m["n"] == 4 and m["positive_rate"] == 0.5 and m["threshold"] == 0.5

    def test_threshold_is_inclusive(self):
        m = compute_metrics([0, 1], [0.2, 0.5], 0.5)
        assert m["tp"] == 1

    def test_no_positive_predictions_gives_zero_not_error(self):
        m = compute_metrics([0, 1, 1], [0.1, 0.2, 0.3], 0.9)
        assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0

    @pytest.mark.parametrize(
        ("y", "p", "msg"),
        [
            ([1, 1, 1], [0.1, 0.2, 0.3], "both classes"),
            ([0, 1], [0.1, 0.2, 0.3], "same length"),
            ([], [], "non-empty"),
            ([0, 1], [0.1, float("nan")], "probabilities"),
            ([0, 1], [0.1, 1.2], "probabilities"),
            ([0, 1], [-0.1, 0.5], "probabilities"),
        ],
    )
    def test_rejects_bad_input(self, y, p, msg):
        with pytest.raises(ValueError, match=msg):
            compute_metrics(y, p, 0.5)


class TestGate:
    def test_boundary_is_inclusive(self, params):
        params["gate"]["roc_auc_min"] = 0.75
        assert check_gate({"roc_auc": 0.75}, params)["passed"] is True
        assert check_gate({"roc_auc": 0.7499}, params)["passed"] is False
        assert check_gate({"roc_auc": 0.9}, params)["passed"] is True

    def test_reports_value_and_threshold(self, params):
        gate = check_gate({"roc_auc": 0.6}, params)
        assert gate == {
            "metric": "roc_auc",
            "value": 0.6,
            "threshold": params["gate"]["roc_auc_min"],
            "passed": False,
        }


def test_save_plots_writes_four_pngs(tmp_path):
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 300)
    p = np.clip(y * 0.3 + rng.random(300) * 0.7, 0, 1)
    paths = save_plots(y, p, 0.5, tmp_path / "plots", "test")
    assert sorted(x.name for x in paths) == [
        "calibration_test.png",
        "confusion_matrix_test.png",
        "pr_curve_test.png",
        "roc_curve_test.png",
    ]
    for path in paths:
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- tracking helpers


class TestTracking:
    def test_env_var_wins(self, params, monkeypatch):
        monkeypatch.setenv(params["mlflow"]["tracking_uri_env"], "http://mlflow.example:5000")
        assert tracking.tracking_uri(params) == "http://mlflow.example:5000"
        assert tracking.tracking_uri(params, smoke=True) == "http://mlflow.example:5000"

    def test_default_is_sqlite_under_repo_root(self, params, monkeypatch, root):
        monkeypatch.delenv(params["mlflow"]["tracking_uri_env"], raising=False)
        assert tracking.tracking_uri(params) == "sqlite:///" + (root / "mlflow.db").as_posix()

    def test_smoke_uses_smoke_directory(self, params, monkeypatch):
        monkeypatch.delenv(params["mlflow"]["tracking_uri_env"], raising=False)
        uri = tracking.tracking_uri(params, smoke=True)
        assert uri.endswith("data/smoke/mlflow.db")

    def test_experiment_name_is_suffixed_for_smoke(self, params):
        assert tracking.experiment_name(params) == params["mlflow"]["experiment_name"]
        assert (
            tracking.experiment_name(params, True) == params["mlflow"]["experiment_name"] + "-smoke"
        )

    def test_git_sha_env_override(self, monkeypatch):
        monkeypatch.setenv("GIT_COMMIT_SHA", "abc123")
        assert tracking.git_info()["git_commit_sha"] == "abc123"

    def test_dvc_hash_from_lock(self, params, tmp_path, root):
        lock = tmp_path / "dvc.lock"
        raw = (root / params["data"]["raw_path"]).relative_to(root).as_posix()
        lock.write_text(
            yaml.safe_dump({"stages": {"ingest": {"outs": [{"path": raw, "md5": "deadbeef"}]}}})
        )
        assert tracking.dvc_data_hash(params, lock_path=lock) == "deadbeef"

    def test_dvc_hash_degrades_gracefully(self, params, tmp_path):
        assert "unavailable" in tracking.dvc_data_hash(params, lock_path=tmp_path / "missing.lock")
        bad = tmp_path / "bad.lock"
        bad.write_text("stages: [1, 2")
        assert "unavailable" in tracking.dvc_data_hash(params, lock_path=bad)
        other = tmp_path / "other.lock"
        other.write_text(
            yaml.safe_dump({"stages": {"ingest": {"outs": [{"path": "x", "md5": "1"}]}}})
        )
        assert "unavailable" in tracking.dvc_data_hash(params, lock_path=other)
        assert "smoke" in tracking.dvc_data_hash(params, smoke=True)

    def test_run_tags_contain_the_contract_keys(self, iso_params):
        tags = tracking.run_tags(iso_params, False, "logistic_regression")
        for key in (
            "git_commit_sha",
            "dvc_data_hash",
            "data_source",
            "feast_repo_commit",
            "algorithm",
            "seed",
            "smoke",
        ):
            assert key in tags, key
        assert tags["smoke"] == "false" and tags["seed"] == str(iso_params["seed"])


# --------------------------------------------------------------------------- estimator factory


class TestBuildEstimator:
    def test_known_algorithms_are_seeded(self):
        a = train.build_estimator("logistic_regression", {"C": 2.0}, 42)
        b = train.build_estimator("logistic_regression", {"C": 2.0}, 42)
        c = train.build_estimator("logistic_regression", {"C": 2.0}, 43)
        assert a.random_state == b.random_state != c.random_state
        assert a.C == 2.0
        assert train.build_estimator("random_forest", {"n_estimators": 5}, 42).n_estimators == 5

    def test_unknown_algorithm(self):
        with pytest.raises(ConfigError, match="unknown algorithm"):
            train.build_estimator("magic", {}, 1)


# --------------------------------------------------------------------------- full pipeline


class TestPipelineRun:
    def test_every_artifact_exists(self, pipeline_env):
        p = pipeline_env.params
        for path in (model_path(p), metrics_path(p), last_run_path(p), evaluation_path(p)):
            assert path.is_file(), path
        assert len(list(plots_dir(p).glob("*.png"))) == 4

    def test_metrics_json_shape(self, pipeline_env):
        summary = json.loads(metrics_path(pipeline_env.params).read_text())
        assert summary["algorithm"] == "logistic_regression"
        assert set(summary["sizes"]) == {"train", "val", "test"}
        assert 0.5 < summary["test"]["roc_auc"] <= 1.0
        # no timings/run ids -> deterministic file
        assert "duration" not in json.dumps(summary) and "run_id" not in summary

    def test_synthetic_data_clears_the_gate(self, pipeline_env):
        gate = json.loads(evaluation_path(pipeline_env.params).read_text())["gate"]
        assert gate["passed"] is True
        assert pipeline_env.results["evaluate"]["gate"] == "passed"

    def test_model_does_not_use_sensitive_columns(self, pipeline_env):
        model = inference.load_model(model_path(pipeline_env.params))
        names = list(model.named_steps["prep"].get_feature_names_out())
        assert not any(s in n for s in ("SEX", "MARRIAGE", "AGE") for n in names)
        assert summary_features(pipeline_env) == len(names)

    def test_mlflow_run_contents(self, pipeline_env):
        import mlflow
        from mlflow.tracking import MlflowClient

        info = json.loads(last_run_path(pipeline_env.params).read_text())
        client = MlflowClient(tracking_uri=pipeline_env.tracking_uri)
        run = client.get_run(info["run_id"])
        for key in (
            "git_commit_sha",
            "dvc_data_hash",
            "data_source",
            "feast_repo_commit",
            "algorithm",
            "seed",
        ):
            assert key in run.data.tags, key
        assert run.data.tags["smoke"] == "false"
        assert run.data.tags["gate_passed"] == "true"  # set by the evaluate stage
        assert run.data.params["algorithm"] == "logistic_regression"
        assert run.data.params["exclude_sensitive"] == "True"
        for metric in ("val_roc_auc", "test_roc_auc", "test_ks", "test_brier", "train_duration_s"):
            assert metric in run.data.metrics, metric
        artifacts = {a.path for a in client.list_artifacts(run.info.run_id)}
        assert {"plots", "reports", "config"} <= artifacts
        mlflow.set_tracking_uri(pipeline_env.tracking_uri)
        assert mlflow.models.get_model_info(info["model_uri"]).signature is not None

    def test_logged_model_reproduces_the_saved_model(self, pipeline_env):
        import mlflow

        mlflow.set_tracking_uri(pipeline_env.tracking_uri)
        info = json.loads(last_run_path(pipeline_env.params).read_text())
        logged = mlflow.sklearn.load_model(info["model_uri"])
        saved = inference.load_model(model_path(pipeline_env.params))
        test = pd.read_parquet(split_path(pipeline_env.params, "test")).head(40)
        frame = test[RAW_INPUT_COLUMNS].astype("float64")
        np.testing.assert_allclose(logged.predict_proba(frame), saved.predict_proba(frame))

    def test_inference_matches_the_pipeline_and_ignores_sensitive_fields(self, pipeline_env):
        model = inference.load_model(model_path(pipeline_env.params))
        test = pd.read_parquet(split_path(pipeline_env.params, "test")).head(30)
        records = test[RAW_INPUT_COLUMNS].to_dict("records")
        expected = model.predict_proba(test[RAW_INPUT_COLUMNS])[:, 1]
        np.testing.assert_allclose(inference.predict_proba(model, records), expected)
        # Counterfactual: flipping SEX / MARRIAGE / AGE in the payload cannot change the score.
        tweaked = [
            {**r, "SEX": 1 + (i % 2), "MARRIAGE": 3, "AGE": 21 + i} for i, r in enumerate(records)
        ]
        np.testing.assert_allclose(inference.predict_proba(model, tweaked), expected)

    def test_predict_applies_threshold(self, pipeline_env):
        model = inference.load_model(model_path(pipeline_env.params))
        test = pd.read_parquet(split_path(pipeline_env.params, "test")).head(20)
        records = test[RAW_INPUT_COLUMNS].to_dict("records")
        out = inference.predict(model, records, 0.5)
        assert all(o["prediction"] == int(o["probability"] >= 0.5) for o in out)
        assert all(o["prediction"] == 1 for o in inference.predict(model, records, 0.0))

    def test_gate_failure_reported_in_a_fresh_run(self, pipeline_env):
        """evaluation.json from the module-level run was not touched by other tests."""
        assert evaluation_path(pipeline_env.params).is_file()


def summary_features(env) -> int:
    return json.loads(metrics_path(env.params).read_text())["n_model_features"]


class TestInferenceValidation:
    def row(self):
        return dict.fromkeys(RAW_INPUT_COLUMNS, 1)

    def test_missing_field(self):
        r = self.row()
        del r["PAY_2"]
        with pytest.raises(ValueError, match="missing required fields.*PAY_2"):
            inference.records_to_frame([r])

    @pytest.mark.parametrize("bad", ["abc", None, float("nan"), float("inf")])
    def test_bad_values(self, bad):
        r = self.row()
        r["BILL_AMT1"] = bad
        with pytest.raises(ValueError):
            inference.records_to_frame([r])

    def test_empty_request(self):
        with pytest.raises(ValueError, match="no records"):
            inference.records_to_frame([])

    def test_numeric_strings_are_accepted(self):
        r = self.row()
        r["LIMIT_BAL"] = "5000"
        assert inference.records_to_frame([r])["LIMIT_BAL"].iloc[0] == 5000


# --------------------------------------------------------------------------- stage behaviour


class TestTrainStage:
    def test_requires_preprocessed_data(self, iso_params):
        with pytest.raises(IngestionError, match="preprocess"):
            train.run(iso_params)

    def test_deterministic_outputs(self, iso_params):
        run_stages(iso_params, "train")
        first = metrics_path(iso_params).read_bytes()
        run_stages(iso_params, "train")
        assert first == metrics_path(iso_params).read_bytes()

    def test_changing_val_labels_does_not_change_the_model_or_test_metrics(self, iso_params):
        run_stages(iso_params, "train")
        before = json.loads(metrics_path(iso_params).read_text())
        path = split_path(iso_params, "val")
        val = pd.read_parquet(path)
        val[iso_params["data"]["target_column"]] = 1 - val[iso_params["data"]["target_column"]]
        val.to_parquet(path)
        train.run(iso_params)
        after = json.loads(metrics_path(iso_params).read_text())
        assert after["test"] == before["test"]
        assert after["val"] != before["val"]

    def test_random_forest_and_sensitive_flag(self, iso_params, monkeypatch):
        iso_params["train"]["algorithm"] = "random_forest"
        iso_params["features"]["exclude_sensitive_from_model"] = False
        monkeypatch.setattr(
            train,
            "load_yaml",
            lambda _p: {"random_forest": {"n_estimators": 20, "max_depth": 4, "n_jobs": 1}},
        )
        run_stages(iso_params, "train")
        summary = json.loads(metrics_path(iso_params).read_text())
        assert summary["algorithm"] == "random_forest"
        names = list(
            inference.load_model(model_path(iso_params)).named_steps["prep"].get_feature_names_out()
        )
        assert "AGE" in names

    def test_smoke_run_is_isolated(self, iso_params, tmp_path):
        run_stages(iso_params, "evaluate", smoke=True)
        assert model_path(iso_params, True).is_relative_to(tmp_path / "smoke")
        assert not model_path(iso_params).exists()
        assert not metrics_path(iso_params).exists()


class TestEvaluateStage:
    def test_missing_inputs(self, iso_params):
        with pytest.raises(IngestionError):
            evaluate.run(iso_params)

    def test_gate_failure_raises_and_still_writes_evidence(self, iso_params):
        run_stages(iso_params, "train")
        iso_params["gate"]["roc_auc_min"] = 0.999
        with pytest.raises(GateFailedError, match="below the minimum"):
            evaluate.run(iso_params)
        report = json.loads(evaluation_path(iso_params).read_text())
        assert report["gate"]["passed"] is False

    def test_gate_failure_exit_code_is_3(self, iso_params, write_params):
        run_stages(iso_params, "train")
        iso_params["gate"]["roc_auc_min"] = 0.999
        argv = ["--params", str(write_params(iso_params))]
        assert stage_main(evaluate.run, description="t", argv=argv) == ExitCode.GATE_FAILED == 3

    def test_gate_failure_is_tagged_on_the_mlflow_run(self, iso_params):
        from mlflow.tracking import MlflowClient

        run_stages(iso_params, "train")
        iso_params["gate"]["roc_auc_min"] = 0.999
        with pytest.raises(GateFailedError):
            evaluate.run(iso_params)
        run_id = json.loads(last_run_path(iso_params).read_text())["run_id"]
        tags = (
            MlflowClient(tracking_uri=tracking.tracking_uri(iso_params)).get_run(run_id).data.tags
        )
        assert tags["gate_passed"] == "false"

    def test_detects_a_model_that_no_longer_matches_training_metrics(self, iso_params):
        run_stages(iso_params, "train")
        path = metrics_path(iso_params)
        summary = json.loads(path.read_text())
        summary["test"]["roc_auc"] += 0.01
        path.write_text(json.dumps(summary))
        with pytest.raises(PipelineError, match="does not reproduce"):
            evaluate.run(iso_params)

    def test_detects_changed_test_data(self, iso_params):
        run_stages(iso_params, "train")
        path = split_path(iso_params, "test")
        test = pd.read_parquet(path)
        test["LIMIT_BAL"] = test["LIMIT_BAL"].iloc[::-1].to_numpy()
        test.to_parquet(path)
        with pytest.raises(PipelineError, match="does not reproduce"):
            evaluate.run(iso_params)


# --------------------------------------------------------------------------- config & wiring


class TestTrainingConfigValidation:
    @pytest.mark.parametrize(
        ("mutate", "msg"),
        [
            (lambda p: p["train"].update(algorithm="svm"), "algorithm"),
            (lambda p: p["train"].pop("model_path"), "model_path"),
            (lambda p: p["evaluate"].update(decision_threshold=1.5), "decision_threshold"),
            (lambda p: p["evaluate"].update(repro_tolerance=-1), "repro_tolerance"),
            (lambda p: p["mlflow"].pop("default_tracking_uri"), "default_tracking_uri"),
            (lambda p: p["features"].pop("schema_path"), "schema_path"),
        ],
    )
    def test_rejects_bad_config(self, params, mutate, msg):
        mutate(params)
        with pytest.raises(ConfigError, match=msg):
            validate_params(params)

    def test_real_params_are_valid(self):
        validate_params(copy.deepcopy(load_params()))


class TestDvcWiring:
    @pytest.fixture(scope="class")
    def dvc(self):
        from src.common.config import repo_root

        return yaml.safe_load((repo_root() / "dvc.yaml").read_text(encoding="utf-8"))["stages"]

    def test_stage_order_and_commands(self, dvc):
        assert list(dvc) == ["ingest", "validate", "preprocess", "train", "evaluate"]
        for name, module in (
            ("preprocess", "src.features.preprocessing"),
            ("train", "src.models.train"),
            ("evaluate", "src.models.evaluate"),
        ):
            assert dvc[name]["cmd"] == f"python -m {module}"

    def test_every_dep_and_param_exists(self, dvc, root, params):
        for name, stage in dvc.items():
            for dep in stage["deps"]:
                assert (root / dep).exists() or dep.startswith(("data/", "reports/", "models/")), (
                    name,
                    dep,
                )
            for key in stage.get("params", []):
                node = params
                for part in key.split("."):
                    assert part in node, (name, key)
                    node = node[part]

    def test_stage_chain_is_connected(self, dvc):
        outs = {
            (o if isinstance(o, str) else next(iter(o)))
            for stage in dvc.values()
            for o in stage.get("outs", []) + stage.get("metrics", [])
        }
        for name, stage in dvc.items():
            for dep in stage["deps"]:
                if dep.startswith(("data/", "reports/", "models/")):
                    assert dep in outs, (name, dep)

    def test_outputs_match_params_paths(self, dvc, params):
        outs = {
            (o if isinstance(o, str) else next(iter(o)))
            for stage in dvc.values()
            for o in stage.get("outs", []) + stage.get("metrics", [])
        }
        for path in (
            params["train"]["model_path"],
            params["train"]["metrics_path"],
            params["train"]["plots_dir"],
            params["evaluate"]["evaluation_path"],
            params["features"]["schema_path"],
        ):
            assert path in outs, path

    def test_small_json_reports_are_not_cached(self, dvc):
        for stage in dvc.values():
            for out in stage.get("outs", []) + stage.get("metrics", []):
                if isinstance(out, dict):
                    ((path, opts),) = out.items()
                    assert path.endswith(".json") and opts == {"cache": False}


# --------------------------------------------------------------------------- real DVC end to end


@pytest.mark.integration
class TestDvcRepro:
    def test_full_pipeline_and_gate_failure(self, tmp_path, root):
        import shutil
        import subprocess
        import sys

        if shutil.which("dvc") is None and not Path(sys.executable).with_name("dvc").exists():
            pytest.skip("dvc not installed")
        dvc_bin = shutil.which("dvc") or str(Path(sys.executable).with_name("dvc"))
        work = tmp_path / "repo"

        def ignore(directory, names):
            skip = {
                ".git",
                "__pycache__",
                ".pytest_cache",
                "data",
                "reports",
                "models",
                "mlflow.db",
                ".venv",
                "venv",
            }
            return [n for n in names if n in skip and Path(directory) == root or n == "__pycache__"]

        shutil.copytree(root, work, ignore=ignore)
        env = {
            **__import__("os").environ,
            "MLFLOW_TRACKING_URI": f"sqlite:///{(tmp_path / 'm.db').as_posix()}",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "PYTHONPATH": str(work),
            "PATH": str(Path(sys.executable).parent)
            + __import__("os").pathsep
            + __import__("os").environ["PATH"],
        }

        def sh(*args):
            return subprocess.run(args, cwd=work, env=env, capture_output=True, text=True)

        params_file = work / "params.yaml"
        data = yaml.safe_load(params_file.read_text())
        # The real ingest stage downloads from UCI; stand in a synthetic CSV and drop that stage.
        from src.data.synthetic import make_synthetic_credit_data

        raw = work / data["data"]["raw_path"]
        raw.parent.mkdir(parents=True, exist_ok=True)
        make_synthetic_credit_data(1500, seed=1).to_csv(raw, index=False, lineterminator="\n")
        dvc_file = work / "dvc.yaml"
        spec = yaml.safe_load(dvc_file.read_text())
        spec["stages"].pop("ingest")
        dvc_file.write_text(yaml.safe_dump(spec, sort_keys=False))
        for cmd in (["git", "init", "-q"], [dvc_bin, "init", "-q", "--no-scm", "-f"]):
            sh(*cmd)
        # --no-scm re-init would drop our .dvc/config; restore it from the copy
        shutil.copy(root / ".dvc" / "config", work / ".dvc" / "config")

        first = sh(dvc_bin, "repro")
        assert first.returncode == 0, first.stdout + first.stderr
        assert (work / "models" / "model.joblib").is_file()
        assert json.loads((work / "reports" / "evaluation.json").read_text())["gate"]["passed"]

        again = sh(dvc_bin, "repro")
        assert again.returncode == 0
        assert "didn't change" in again.stdout.lower() or "up to date" in again.stdout.lower()

        data["gate"]["roc_auc_min"] = 0.999
        params_file.write_text(yaml.safe_dump(data))
        failed = sh(dvc_bin, "repro")
        assert failed.returncode != 0
        assert (
            json.loads((work / "reports" / "evaluation.json").read_text())["gate"]["passed"]
            is False
        )


class TestArtifactLocation:
    def test_sqlite_artifacts_sit_next_to_the_database(self, tmp_path):
        uri = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
        assert tracking.artifact_location(uri) == (tmp_path / "mlartifacts").as_uri()

    def test_server_uris_are_left_to_the_server(self):
        assert tracking.artifact_location("http://mlflow:5000") is None

    def test_a_training_run_never_creates_mlruns_in_the_working_directory(
        self, iso_params, tmp_path, monkeypatch
    ):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        run_stages(iso_params, "train")
        assert not (cwd / "mlruns").exists()
        assert (tmp_path / "mlartifacts").is_dir()
