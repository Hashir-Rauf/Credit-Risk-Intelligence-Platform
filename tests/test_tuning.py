"""Cycle 3 tests: Optuna tuning, model selection, registry aliases and promotion."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from src.common.cli import ExitCode, stage_main
from src.common.config import validate_params
from src.common.errors import ConfigError, GateFailedError, IngestionError, PipelineError
from src.data.paths import (
    comparison_path,
    evaluation_path,
    last_run_path,
    metrics_path,
    promotion_path,
    registration_path,
    split_path,
    tuning_path,
)
from src.models import inference, promote, registry, train, tune
from src.models.evaluate import check_regression
from src.models.tracking import registry_model_name

from conftest import run_stages

FAMILIES = ["logistic_regression", "random_forest", "hist_gradient_boosting"]


@pytest.fixture()
def tuned_params(iso_params, monkeypatch):
    """Params with `tuned` training and tiny, fast search spaces."""
    iso_params["train"]["algorithm"] = "tuned"
    iso_params["tune"].update(n_trials=3, smoke_n_trials=2)
    config = {
        "logistic_regression": {"max_iter": 300, "class_weight": "balanced"},
        "random_forest": {"n_estimators": 20, "max_depth": 4, "n_jobs": 1},
        "hist_gradient_boosting": {"max_iter": 30, "early_stopping": False},
        "search_spaces": {
            "logistic_regression": {"C": {"type": "float", "low": 0.01, "high": 10.0, "log": True}},
            "random_forest": {"max_depth": {"type": "int", "low": 2, "high": 6}},
            "hist_gradient_boosting": {
                "learning_rate": {"type": "float", "low": 0.03, "high": 0.3, "log": True},
                "max_depth": {"type": "int", "low": 2, "high": 4},
            },
        },
    }
    monkeypatch.setattr(tune, "load_yaml", lambda _p: config)
    monkeypatch.setattr(train, "load_yaml", lambda _p: config)
    return iso_params


# --------------------------------------------------------------------------- search helpers


class FakeTrial:
    def __init__(self):
        self.calls = []

    def suggest_float(self, name, low, high, log=False):
        self.calls.append(("float", name, low, high, log))
        return low

    def suggest_int(self, name, low, high, step=1, log=False):
        self.calls.append(("int", name, low, high, step, log))
        return low

    def suggest_categorical(self, name, choices):
        self.calls.append(("cat", name, tuple(choices)))
        return choices[0]


class TestSuggest:
    def test_each_spec_type(self):
        trial = FakeTrial()
        assert (
            tune.suggest(trial, "C", {"type": "float", "low": 0.1, "high": 9, "log": True}) == 0.1
        )
        assert tune.suggest(trial, "d", {"type": "int", "low": 2, "high": 8, "step": 2}) == 2
        assert (
            tune.suggest(trial, "w", {"type": "categorical", "choices": ["balanced", None]})
            == "balanced"
        )
        assert trial.calls[0] == ("float", "C", 0.1, 9, True)
        assert trial.calls[1] == ("int", "d", 2, 8, 2, False)

    def test_log_int_ignores_step(self):
        trial = FakeTrial()
        tune.suggest(trial, "n", {"type": "int", "low": 1, "high": 30, "step": 5, "log": True})
        assert trial.calls[0] == ("int", "n", 1, 30, 1, True)

    def test_unknown_type(self):
        with pytest.raises(ConfigError, match="unknown type"):
            tune.suggest(FakeTrial(), "x", {"type": "weird"})

    def test_sampled_values_override_defaults_and_keep_fixed_ones(self):
        out = tune.sample_hyperparameters(
            FakeTrial(),
            {"max_iter": 100, "C": 1.0},
            {"C": {"type": "float", "low": 0.5, "high": 2}},
        )
        assert out == {"max_iter": 100, "C": 0.5}


class TestSelectWinner:
    def result(self, **values):
        return {f: {"best_value": v} for f, v in values.items()}

    def test_best_validation_auc_wins(self):
        winner, reason = tune.select_winner(
            self.result(logistic_regression=0.76, random_forest=0.78, hist_gradient_boosting=0.79),
            0.002,
        )
        assert winner == "hist_gradient_boosting" and "highest validation" in reason

    def test_simpler_model_wins_within_margin(self):
        winner, reason = tune.select_winner(
            self.result(
                logistic_regression=0.7790, random_forest=0.7800, hist_gradient_boosting=0.7805
            ),
            0.002,
        )
        assert winner == "logistic_regression" and "simpler" in reason

    def test_margin_boundary_is_inclusive_and_zero_margin_means_best(self):
        data = self.result(logistic_regression=0.7780, hist_gradient_boosting=0.7800)
        assert tune.select_winner(data, 0.002)[0] == "logistic_regression"
        assert tune.select_winner(data, 0.0019)[0] == "hist_gradient_boosting"
        assert tune.select_winner(data, 0.0)[0] == "hist_gradient_boosting"

    def test_exact_tie_prefers_simpler(self):
        assert (
            tune.select_winner(self.result(random_forest=0.8, hist_gradient_boosting=0.8), 0.0)[0]
            == "random_forest"
        )


# --------------------------------------------------------------------------- tune stage


class TestTuneStage:
    def test_report_contents(self, tuned_params):
        run_stages(tuned_params, "tune")
        report = json.loads(tuning_path(tuned_params).read_text())
        assert sorted(report["families"]) == sorted(FAMILIES)
        assert report["n_trials"] == 3 and report["metric"] == "val_roc_auc"
        for result in report["families"].values():
            assert len(result["trials"]) == 3 and result["n_trials"] == 3
            assert result["best_value"] == max(t["val_roc_auc"] for t in result["trials"])
        selected = report["selected"]
        assert selected["algorithm"] in FAMILIES
        assert (
            selected["hyperparameters"]
            == report["families"][selected["algorithm"]]["best_hyperparameters"]
        )
        assert 0.5 < selected["val_roc_auc"] <= 1.0

    def test_cv_stability_check_reported(self, tuned_params):
        tuned_params["tune"]["cv_folds"] = 3
        run_stages(tuned_params, "tune")
        report = json.loads(tuning_path(tuned_params).read_text())
        assert report["cv_folds"] == 3
        for result in report["families"].values():
            assert result["cv_folds"] == 3
            assert 0.5 < result["cv_roc_auc_mean"] <= 1.0 and result["cv_roc_auc_std"] >= 0
        assert "Train CV ROC-AUC" in comparison_path(tuned_params).read_text()

    def test_cv_never_changes_the_winner_or_search(self, tuned_params):
        tuned_params["tune"]["cv_folds"] = 0
        run_stages(tuned_params, "tune")
        off = json.loads(tuning_path(tuned_params).read_text())
        tuned_params["tune"]["cv_folds"] = 3
        tune.run(tuned_params)
        on = json.loads(tuning_path(tuned_params).read_text())
        assert off["selected"] == on["selected"]
        for family in off["families"]:
            assert off["families"][family]["trials"] == on["families"][family]["trials"]
            assert off["families"][family]["cv_roc_auc_mean"] is None

    def test_cv_off_is_marked_in_the_comparison(self, tuned_params):
        tuned_params["tune"]["cv_folds"] = 0
        run_stages(tuned_params, "tune")
        assert "| off |" in comparison_path(tuned_params).read_text()

    def test_cross_validate_best_reads_only_train(self, tuned_params):
        run_stages(tuned_params, "feast")
        splits = tune.load_splits(tuned_params, False)
        target = tuned_params["data"]["target_column"]
        columns = tune.model_input_columns(tuned_params)
        data = {
            "params": tuned_params,
            "x_train": splits["train"][columns],
            "y_train": splits["train"][target],
            "x_val": None,  # would raise if touched
            "y_val": None,
        }
        hp = {"C": 1.0, "max_iter": 200}
        first = tune.cross_validate_best("logistic_regression", hp, data, 42, 3)
        assert first == tune.cross_validate_best("logistic_regression", hp, data, 42, 3)
        assert first["cv_folds"] == 3 and 0.5 < first["cv_roc_auc_mean"] <= 1.0
        assert tune.cross_validate_best("logistic_regression", hp, data, 42, 0) == {
            "cv_folds": 0,
            "cv_roc_auc_mean": None,
            "cv_roc_auc_std": None,
        }

    def test_best_hyperparameters_merge_fixed_defaults(self, tuned_params):
        run_stages(tuned_params, "tune")
        families = json.loads(tuning_path(tuned_params).read_text())["families"]
        assert families["random_forest"]["best_hyperparameters"]["n_estimators"] == 20
        assert "max_depth" in families["random_forest"]["best_hyperparameters"]

    def test_comparison_markdown(self, tuned_params):
        run_stages(tuned_params, "tune")
        text = comparison_path(tuned_params).read_text()
        assert (
            "# Model comparison" in text and "validation" in text and "test split untouched" in text
        )
        for family in FAMILIES:
            assert family in text
        assert "**Selected:" in text and "\r" not in text

    def test_outputs_are_deterministic(self, tuned_params):
        run_stages(tuned_params, "tune")
        first = (tuning_path(tuned_params).read_bytes(), comparison_path(tuned_params).read_bytes())
        tune.run(tuned_params)
        assert first == (
            tuning_path(tuned_params).read_bytes(),
            comparison_path(tuned_params).read_bytes(),
        )

    def test_seed_changes_the_search(self, tuned_params):
        run_stages(tuned_params, "tune")
        first = json.loads(tuning_path(tuned_params).read_text())["families"]["random_forest"][
            "trials"
        ]
        tuned_params["seed"] = 7
        tune.run(tuned_params)
        second = json.loads(tuning_path(tuned_params).read_text())["families"]["random_forest"][
            "trials"
        ]
        assert [t["params"] for t in first] != [t["params"] for t in second] or first != second

    def test_never_reads_the_test_split(self, tuned_params):
        """Replacing test labels with garbage cannot change the tuning outcome."""
        run_stages(tuned_params, "preprocess")
        tune.run(tuned_params)
        before = tuning_path(tuned_params).read_bytes()
        path = split_path(tuned_params, "test")
        test = pd.read_parquet(path)
        test[tuned_params["data"]["target_column"]] = (
            1 - test[tuned_params["data"]["target_column"]]
        )
        test.to_parquet(path)
        tune.run(tuned_params)
        assert before == tuning_path(tuned_params).read_bytes()

    def test_smoke_uses_fewer_trials_and_stays_in_smoke_dir(self, tuned_params, tmp_path):
        run_stages(tuned_params, "tune", smoke=True)
        report = json.loads(tuning_path(tuned_params, smoke=True).read_text())
        assert report["n_trials"] == 2 and report["smoke"] is True
        assert tuning_path(tuned_params, smoke=True).is_relative_to(tmp_path / "smoke")
        assert not tuning_path(tuned_params).exists()
        assert "Smoke run" in comparison_path(tuned_params, smoke=True).read_text()

    def test_mlflow_has_one_parent_and_one_nested_run_per_family(self, tuned_params):
        from mlflow.tracking import MlflowClient
        from src.models.tracking import experiment_name, tracking_uri

        run_stages(tuned_params, "tune")
        client = MlflowClient(tracking_uri=tracking_uri(tuned_params))
        experiment = client.get_experiment_by_name(experiment_name(tuned_params))
        runs = client.search_runs([experiment.experiment_id])
        by_type = {}
        for r in runs:
            by_type.setdefault(r.data.tags.get("run_type"), []).append(r)
        assert len(by_type["tuning"]) == 1 and len(by_type["tuning_family"]) == 3
        parent = by_type["tuning"][0]
        assert {r.data.tags["mlflow.parentRunId"] for r in by_type["tuning_family"]} == {
            parent.info.run_id
        }
        assert "best_val_roc_auc" in parent.data.metrics
        assert {a.path for a in client.list_artifacts(parent.info.run_id)} == {"reports"}

    def test_missing_search_space(self, tuned_params, monkeypatch):
        monkeypatch.setattr(tune, "load_yaml", lambda _p: {"search_spaces": {}})
        run_stages(tuned_params, "preprocess")
        with pytest.raises(ConfigError, match="no search space"):
            tune.run(tuned_params)

    def test_requires_preprocessed_data(self, tuned_params):
        with pytest.raises(IngestionError, match="preprocess"):
            tune.run(tuned_params)


# --------------------------------------------------------------------------- train with tuned


class TestTrainTuned:
    def test_trains_the_selected_model_with_its_hyperparameters(self, tuned_params):
        run_stages(tuned_params, "evaluate")
        selected = json.loads(tuning_path(tuned_params).read_text())["selected"]
        summary = json.loads(metrics_path(tuned_params).read_text())
        assert summary["tuned"] is True
        assert summary["algorithm"] == selected["algorithm"]
        assert summary["hyperparameters"] == selected["hyperparameters"]
        # train reproduces the validation AUC the search found for the winner
        assert summary["val"]["roc_auc"] == pytest.approx(selected["val_roc_auc"], abs=1e-9)

    def test_mlflow_training_run_is_marked_tuned_and_carries_the_comparison(self, tuned_params):
        from mlflow.tracking import MlflowClient
        from src.models.tracking import tracking_uri

        run_stages(tuned_params, "train")
        run_id = json.loads(last_run_path(tuned_params).read_text())["run_id"]
        client = MlflowClient(tracking_uri=tracking_uri(tuned_params))
        run = client.get_run(run_id)
        assert run.data.params["tuned"] == "True" and run.data.tags["run_type"] == "training"
        files = {a.path for a in client.list_artifacts(run_id, "reports")}
        assert {"reports/tuning.json", "reports/model_comparison.md"} <= files

    def test_requires_the_tune_stage(self, tuned_params):
        run_stages(tuned_params, "preprocess")
        with pytest.raises(IngestionError, match="tune stage"):
            train.run(tuned_params)

    @pytest.mark.parametrize("family", ["random_forest", "hist_gradient_boosting"])
    def test_every_family_trains_logs_and_loads_through_mlflow(self, tuned_params, family):
        import mlflow
        from src.models.tracking import tracking_uri

        tuned_params["train"]["algorithm"] = family
        run_stages(tuned_params, "train")
        info = json.loads(last_run_path(tuned_params).read_text())
        mlflow.set_tracking_uri(tracking_uri(tuned_params))
        model = mlflow.sklearn.load_model(info["model_uri"])
        test = pd.read_parquet(split_path(tuned_params, "test")).head(10)
        proba = model.predict_proba(test[inference.RAW_INPUT_COLUMNS].astype("float64"))
        assert proba.shape == (10, 2)

    def test_hist_gradient_boosting_never_sees_sensitive_columns(self, tuned_params):
        from src.models.train import resolve_model_choice

        tuned_params["train"]["algorithm"] = "hist_gradient_boosting"
        assert resolve_model_choice(tuned_params, False)[2] is False
        run_stages(tuned_params, "train")
        from src.data.paths import model_path

        names = list(
            inference.load_model(model_path(tuned_params))
            .named_steps["prep"]
            .get_feature_names_out()
        )
        assert not {"SEX", "AGE", "MARRIAGE"} & set(names)


# --------------------------------------------------------------------------- regression helper


class TestCheckRegression:
    base = {"roc_auc": 0.78, "pr_auc": 0.55, "ks": 0.42}

    def test_no_regression(self):
        assert check_regression(self.base, self.base, 0.01) == []
        assert check_regression({"roc_auc": 0.9, "pr_auc": 0.7, "ks": 0.6}, self.base, 0.0) == []

    def test_reports_every_metric_that_dropped_too_far(self):
        candidate = {"roc_auc": 0.76, "pr_auc": 0.54, "ks": 0.30}
        assert check_regression(candidate, self.base, 0.01) == ["roc_auc", "ks"]

    def test_boundary_drop_is_allowed(self):
        candidate = {"roc_auc": 0.77, "pr_auc": 0.54, "ks": 0.41}
        assert check_regression(candidate, self.base, 0.01) == []


# --------------------------------------------------------------------------- promotion decision


class TestDecide:
    base = {"roc_auc": 0.78, "pr_auc": 0.55, "ks": 0.42}

    def better(self, **kw):
        return {**self.base, **{"roc_auc": 0.79, **kw}}

    def test_first_champion(self, params):
        d = promote.decide(self.base, None, params)
        assert (
            d["promote"] is True
            and d["reasons"] == ["no champion yet"]
            and d["roc_auc_gain"] is None
        )

    def test_clear_win(self, params):
        d = promote.decide(self.better(), self.base, params)
        assert d["promote"] is True and d["reasons"] == []
        assert d["roc_auc_gain"] == pytest.approx(0.01)

    def test_gain_below_minimum_is_rejected(self, params):
        d = promote.decide(self.better(roc_auc=0.7830), self.base, params)
        assert d["promote"] is False and "below the required" in d["reasons"][0]

    def test_gain_exactly_at_minimum_passes(self, params):
        params["promotion"]["min_auc_gain"] = 0.01
        assert promote.decide(self.better(roc_auc=0.79), self.base, params)["promote"] is True

    def test_gain_exactly_equal_to_minimum_passes_with_exact_floats(self, params):
        params["promotion"]["min_auc_gain"] = 0.25
        champion = {"roc_auc": 0.5, "pr_auc": 0.5, "ks": 0.5}
        candidate = {"roc_auc": 0.75, "pr_auc": 0.5, "ks": 0.5}
        assert promote.decide(candidate, champion, params)["promote"] is True
        candidate["roc_auc"] = 0.5
        params["promotion"]["min_auc_gain"] = 0.0
        assert promote.decide(candidate, champion, params)["promote"] is True

    def test_a_regression_elsewhere_blocks_even_with_an_auc_gain(self, params):
        d = promote.decide(self.better(ks=0.30), self.base, params)
        assert d["promote"] is False and d["regressed_metrics"] == ["ks"]

    def test_fairness_regression_blocks_only_when_the_check_is_on(self, params):
        bad = {"regressions": ["SEX.tpr_gap"], "failed_checks": []}
        d = promote.decide(self.better(), self.base, params, bad)
        assert d["promote"] is False and "fairness regression" in d["reasons"][0]
        assert "SEX.tpr_gap" in d["reasons"][0]
        params["promotion"]["fairness_check"] = False
        assert promote.decide(self.better(), self.base, params, bad)["promote"] is True

    def test_failed_absolute_fairness_checks_block(self, params):
        failing = {"regressions": [], "failed_checks": ["AGE_BAND.disparate_impact"]}
        d = promote.decide(self.better(), self.base, params, failing)
        assert d["promote"] is False and "fairness checks failed" in d["reasons"][0]

    def test_clean_or_missing_fairness_input_does_not_block(self, params):
        assert promote.decide(self.better(), self.base, params, None)["promote"] is True
        clean = {"regressions": [], "failed_checks": []}
        assert promote.decide(self.better(), self.base, params, clean)["promote"] is True

    def test_worse_candidate_is_rejected(self, params):
        assert (
            promote.decide({"roc_auc": 0.7, "pr_auc": 0.4, "ks": 0.3}, self.base, params)["promote"]
            is False
        )


# --------------------------------------------------------------------------- registry + promote


@pytest.fixture()
def trained(tuned_params):
    tuned_params["train"]["algorithm"] = "logistic_regression"
    run_stages(tuned_params, "evaluate")
    return tuned_params


def retrain(params, algorithm="logistic_regression", **hyper):
    """Train another candidate (different hyperparameters) through the real stages."""
    import src.models.train as train_module

    original = train_module.resolve_model_choice
    train_module.resolve_model_choice = lambda p, s: (algorithm, hyper, False)
    try:
        train_module.run(params)
        from src.models import evaluate

        evaluate.run(params)
    finally:
        train_module.resolve_model_choice = original


class TestRegistry:
    def test_registers_and_sets_the_challenger_alias(self, trained):
        record = registry.register_candidate(trained)
        assert record["alias"] == "challenger" and record["version"] == "1"
        assert record["model_name"] == registry_model_name(trained)
        assert json.loads(registration_path(trained).read_text()) == record
        version = registry.alias_version(trained, "challenger")
        assert str(version.version) == "1"
        for tag in (
            "gate_passed",
            "test_roc_auc",
            "algorithm",
            "git_commit_sha",
            "dvc_data_hash",
            "source_run_id",
        ):
            assert tag in version.tags, tag
        assert version.tags["gate_passed"] == "true"

    def test_registering_twice_is_idempotent(self, trained):
        registry.register_candidate(trained)
        again = registry.register_candidate(trained)
        assert again["version"] == "1"

    def test_a_new_training_run_gets_a_new_version(self, trained):
        registry.register_candidate(trained)
        retrain(trained, C=0.5, max_iter=300)
        assert registry.register_candidate(trained)["version"] == "2"
        assert str(registry.alias_version(trained, "challenger").version) == "2"

    def test_loaded_model_scores_like_the_saved_one(self, trained):
        from src.data.paths import model_path

        registry.register_candidate(trained)
        loaded = registry.load_by_alias(trained, "challenger")
        saved = inference.load_model(model_path(trained))
        frame = pd.read_parquet(split_path(trained, "test")).head(25)[inference.RAW_INPUT_COLUMNS]
        np.testing.assert_allclose(
            loaded.predict_proba(frame.astype("float64")),
            saved.predict_proba(frame.astype("float64")),
        )

    def test_failed_gate_is_refused(self, trained):
        report = json.loads(evaluation_path(trained).read_text())
        report["gate"]["passed"] = False
        evaluation_path(trained).write_text(json.dumps(report))
        with pytest.raises(GateFailedError, match="cannot be registered"):
            registry.register_candidate(trained)
        assert registry.alias_version(trained, "challenger") is None

    def test_stale_evaluation_is_refused(self, trained):
        report = json.loads(evaluation_path(trained).read_text())
        report["test_metrics"]["roc_auc"] -= 0.05
        evaluation_path(trained).write_text(json.dumps(report))
        with pytest.raises(PipelineError, match="stale"):
            registry.register_candidate(trained)

    def test_missing_inputs(self, tuned_params):
        with pytest.raises(IngestionError, match="train and evaluate"):
            registry.register_candidate(tuned_params)

    def test_unknown_alias(self, trained):
        assert registry.alias_version(trained, "champion") is None
        with pytest.raises(IngestionError, match="not set"):
            registry.load_by_alias(trained, "champion")

    def test_passes_gate_helper(self, params):
        assert registry.passes_gate({"roc_auc": params["gate"]["roc_auc_min"]}, params) is True
        assert registry.passes_gate({"roc_auc": 0.5}, params) is False

    def test_smoke_uses_a_separate_model_name(self, trained):
        assert registry_model_name(trained, smoke=True) == registry_model_name(trained) + "-smoke"

    def test_cli_exit_codes(self, trained, write_params):
        argv = ["--params", str(write_params(trained))]
        assert stage_main(registry.run, description="t", argv=argv) == 0
        report = json.loads(evaluation_path(trained).read_text())
        report["gate"]["passed"] = False
        evaluation_path(trained).write_text(json.dumps(report))
        assert stage_main(registry.run, description="t", argv=argv) == ExitCode.GATE_FAILED


class TestPromote:
    def test_first_candidate_becomes_champion_and_challenger_alias_is_cleared(self, trained):
        registry.register_candidate(trained)
        result = promote.run(trained)
        assert result["champion_version"] == "1"
        assert str(registry.alias_version(trained, "champion").version) == "1"
        assert registry.alias_version(trained, "challenger") is None
        decision = json.loads(promotion_path(trained).read_text())
        assert decision["promote"] is True and decision["champion_version"] is None

    def test_requires_a_challenger(self, trained):
        with pytest.raises(IngestionError, match="no 'challenger'"):
            promote.run(trained)

    def test_equal_challenger_is_rejected_and_champion_untouched(self, trained):
        registry.register_candidate(trained)
        promote.run(trained)
        retrain(
            trained, C=1.0, max_iter=300, class_weight="balanced"
        )  # same model again -> same scores -> no gain
        registry.register_candidate(trained)
        with pytest.raises(GateFailedError, match="not promoted"):
            promote.run(trained)
        assert str(registry.alias_version(trained, "champion").version) == "1"
        assert str(registry.alias_version(trained, "challenger").version) == "2"
        decision = json.loads(promotion_path(trained).read_text())
        assert decision["promote"] is False and decision["roc_auc_gain"] == pytest.approx(
            0.0, abs=1e-9
        )
        assert (
            registry.alias_version(trained, "challenger").tags["promotion_decision"] == "rejected"
        )

    def test_better_challenger_replaces_the_champion(self, trained):
        trained["promotion"]["min_auc_gain"] = 0.0
        trained["promotion"]["max_regression_drop"] = 0.5
        trained["gate"]["roc_auc_min"] = 0.5  # the weak champion must still be registrable
        trained["promotion"]["fairness_check"] = False  # this test is about the AUC rules only
        retrain(trained, "random_forest", n_estimators=1, max_depth=1)  # deliberately weak champion
        registry.register_candidate(trained)
        promote.run(trained)
        retrain(trained, C=1.0, max_iter=300, class_weight="balanced")
        registry.register_candidate(trained)
        trained["promotion"]["min_auc_gain"] = 0.0001
        result = promote.run(trained)
        assert result["champion_version"] == "2" and result["roc_auc_gain"] > 0
        assert str(registry.alias_version(trained, "champion").version) == "2"
        assert registry.alias_version(trained, "challenger") is None

    def test_promoting_the_current_champion_again_is_an_error(self, trained):
        registry.register_candidate(trained)
        promote.run(trained)
        registry.set_alias(trained, "challenger", 1)
        with pytest.raises(IngestionError, match="already the champion"):
            promote.run(trained)

    def test_a_fairness_regression_blocks_an_otherwise_better_challenger(
        self, trained, monkeypatch
    ):
        trained["promotion"]["min_auc_gain"] = 0.0
        trained["promotion"]["max_regression_drop"] = 0.5
        trained["gate"]["roc_auc_min"] = 0.5
        retrain(trained, "random_forest", n_estimators=1, max_depth=1)  # weak champion
        registry.register_candidate(trained)
        promote.run(trained)
        retrain(trained, C=1.0, max_iter=300, class_weight="balanced")
        registry.register_candidate(trained)
        monkeypatch.setattr(promote, "fairness_regressions", lambda *a: ["SEX.approval_rate_gap"])
        with pytest.raises(GateFailedError, match="fairness regression"):
            promote.run(trained)
        assert str(registry.alias_version(trained, "champion").version) == "1"
        decision = json.loads(promotion_path(trained).read_text())
        assert decision["fairness"]["regressions"] == ["SEX.approval_rate_gap"]

    def test_the_decision_records_the_fairness_comparison(self, trained):
        registry.register_candidate(trained)
        promote.run(trained)
        decision = json.loads(promotion_path(trained).read_text())
        assert decision["fairness"] == {"regressions": [], "failed_checks": []}

    def test_cli_exit_code_when_not_promoted(self, trained, write_params):
        registry.register_candidate(trained)
        promote.run(trained)
        retrain(trained, C=1.0, max_iter=300, class_weight="balanced")
        registry.register_candidate(trained)
        argv = ["--params", str(write_params(trained))]
        assert stage_main(promote.run, description="t", argv=argv) == ExitCode.GATE_FAILED


# --------------------------------------------------------------------------- config / wiring


class TestConfig:
    @pytest.mark.parametrize(
        ("mutate", "msg"),
        [
            (lambda p: p["tune"].update(families=[]), "tune.families"),
            (lambda p: p["tune"].update(families=["svm"]), "tune.families"),
            (lambda p: p["tune"].update(n_trials=0), "tune.n_trials"),
            (lambda p: p["tune"].update(smoke_n_trials=1.5), "tune.smoke_n_trials"),
            (lambda p: p["tune"].update(simplicity_margin=0.5), "simplicity_margin"),
            (lambda p: p["promotion"].update(min_auc_gain=-0.1), "min_auc_gain"),
            (lambda p: p["promotion"].update(max_regression_drop="x"), "max_regression_drop"),
            (
                lambda p: p["promotion"].update(fairness_check="yes"),
                "fairness_check",
            ),
            (lambda p: p["promotion"].pop("decision_path"), "decision_path"),
            (lambda p: p["train"].update(algorithm="xgboost"), "algorithm"),
        ],
    )
    def test_rejects_bad_values(self, params, mutate, msg):
        mutate(params)
        with pytest.raises(ConfigError, match=msg):
            validate_params(params)

    def test_real_defaults_meet_the_spec(self, params):
        assert params["tune"]["n_trials"] >= 20  # spec: at least 20 trials
        assert set(params["tune"]["families"]) == set(FAMILIES)
        assert params["train"]["algorithm"] == "tuned"

    def test_every_family_has_a_search_space_and_defaults(self):
        from src.common.config import load_yaml

        config = load_yaml("configs/model_params.yaml")
        for family in FAMILIES:
            assert config[family] and config["search_spaces"][family]
