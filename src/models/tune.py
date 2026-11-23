"""Hyperparameter search (Optuna) across model families, and selection of the winner.

    python -m src.models.tune [--params params.yaml] [--smoke]

For each family in `tune.families` an Optuna TPE study maximises ROC-AUC on the validation
split (model fitted on train only; the test split is never read here). The winner, its
hyperparameters and the comparison table are written to `reports/tuning.json` and
`reports/model_comparison.md`; `train` refits the winner when `train.algorithm: tuned`.
After the search, the best hyperparameters of each family get a stratified k-fold CV AUC on
train (`tune.cv_folds`) as a stability check; it does not affect selection.
Everything is seeded from params.yaml `seed`, so the same inputs give the same winner.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

from src.common.cli import stage_main
from src.common.config import load_yaml
from src.common.errors import ConfigError
from src.common.io import atomic_write_bytes, write_json
from src.common.logging_utils import get_logger
from src.common.seed import derive_seed
from src.data.paths import comparison_path, tuning_path
from src.features.preprocessing import SPLITS, build_pipeline, model_input_columns
from src.models.tracking import experiment_name, run_tags, tracking_uri
from src.models.train import MODEL_PARAMS_FILE, build_estimator, load_splits

log = get_logger(__name__)

# Tie-break order when several families are within `tune.simplicity_margin` of the best.
SIMPLICITY_ORDER = ("logistic_regression", "random_forest", "hist_gradient_boosting")


def suggest(trial: Any, name: str, spec: dict[str, Any]) -> Any:
    """One value for hyperparameter `name` from its search-space spec."""
    kind = spec.get("type")
    if kind == "float":
        return trial.suggest_float(
            name, spec["low"], spec["high"], log=bool(spec.get("log", False))
        )
    if kind == "int":
        step = 1 if spec.get("log") else spec.get("step", 1)
        return trial.suggest_int(
            name, spec["low"], spec["high"], step=step, log=bool(spec.get("log", False))
        )
    if kind == "categorical":
        return trial.suggest_categorical(name, spec["choices"])
    raise ConfigError(f"search space for {name!r}: unknown type {kind!r}")


def sample_hyperparameters(
    trial: Any, defaults: dict[str, Any], space: dict[str, Any]
) -> dict[str, Any]:
    """Fixed defaults overridden by the values Optuna suggests for the searched keys."""
    return {**defaults, **{name: suggest(trial, name, spec) for name, spec in space.items()}}


def select_winner(results: dict[str, dict[str, Any]], margin: float) -> tuple[str, str]:
    """(family, reason): best validation AUC, unless a simpler family is within `margin`."""
    ranked = sorted(results, key=lambda f: (-results[f]["best_value"], SIMPLICITY_ORDER.index(f)))
    best = ranked[0]
    best_value = results[best]["best_value"]
    close = [f for f in results if best_value - results[f]["best_value"] <= margin + 1e-12]
    winner = min(close, key=SIMPLICITY_ORDER.index)
    if winner == best:
        return winner, f"highest validation ROC-AUC ({best_value:.4f})"
    return winner, (
        f"{winner} is within {margin:g} validation ROC-AUC of {best} "
        f"({results[winner]['best_value']:.4f} vs {best_value:.4f}) and is the simpler model"
    )


def _tune_family(
    family: str,
    defaults: dict[str, Any],
    space: dict[str, Any],
    data: dict[str, Any],
    n_trials: int,
    seed: int,
    cv_folds: int = 0,
) -> tuple[Any, dict[str, Any]]:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=derive_seed(seed, f"tune:{family}")),
    )

    def objective(trial: Any) -> float:
        hyperparameters = sample_hyperparameters(trial, defaults, space)
        pipeline = build_pipeline(data["params"], build_estimator(family, hyperparameters, seed))
        pipeline.fit(data["x_train"], data["y_train"])
        return float(roc_auc_score(data["y_val"], pipeline.predict_proba(data["x_val"])[:, 1]))

    study.optimize(objective, n_trials=n_trials)
    best = {**defaults, **study.best_params}
    cv = cross_validate_best(family, best, data, seed, cv_folds)
    return study, {
        "best_value": float(study.best_value),
        "best_hyperparameters": best,
        **cv,
        "n_trials": len(study.trials),
        "trials": [
            {"number": t.number, "val_roc_auc": float(t.value), "params": dict(t.params)}
            for t in study.trials
        ],
    }


def cross_validate_best(
    family: str, hyperparameters: dict[str, Any], data: dict[str, Any], seed: int, folds: int
) -> dict[str, Any]:
    """Stratified k-fold ROC-AUC on the TRAIN split for the best hyperparameters of a family.

    A robustness check next to the single-split validation AUC the search optimises: the mean
    shows whether the winner holds up across folds, the std how noisy the estimate is. It never
    influences which trial or family wins (selection stays on validation AUC), and it never
    touches the validation or test splits. `folds` of 0 switches it off.
    """
    if folds < 2:
        return {"cv_folds": 0, "cv_roc_auc_mean": None, "cv_roc_auc_std": None}
    pipeline = build_pipeline(data["params"], build_estimator(family, hyperparameters, seed))
    splitter = StratifiedKFold(
        n_splits=folds, shuffle=True, random_state=derive_seed(seed, f"cv:{family}")
    )
    scores = cross_val_score(
        pipeline, data["x_train"], data["y_train"], cv=splitter, scoring="roc_auc"
    )
    return {
        "cv_folds": folds,
        "cv_roc_auc_mean": float(np.mean(scores)),
        "cv_roc_auc_std": float(np.std(scores)),
    }


def comparison_markdown(report: dict[str, Any]) -> str:
    """Human-readable comparison + selection reason (the evidence for the written justification)."""
    selected = report["selected"]
    lines = [
        "# Model comparison",
        "",
        "Metric: ROC-AUC on the **validation** split "
        "(models fitted on train only; test split untouched). "
        f"Trials per family: {report['n_trials']}. Seed: {report['seed']}."
        + (" **Smoke run: synthetic data, not a result.**" if report["smoke"] else ""),
        "",
        "| Family | Best val ROC-AUC | Train CV ROC-AUC (mean +/- std) | Trials | Selected |",
        "|---|---|---|---|---|",
    ]
    for family, result in report["families"].items():
        mark = "yes" if family == selected["algorithm"] else ""
        cv = (
            f"{result['cv_roc_auc_mean']:.4f} +/- {result['cv_roc_auc_std']:.4f} "
            f"({result['cv_folds']} folds)"
            if result.get("cv_roc_auc_mean") is not None
            else "off"
        )
        lines.append(
            f"| {family} | {result['best_value']:.4f} | {cv} | {result['n_trials']} | {mark} |"
        )
    lines += [
        "",
        f"**Selected: {selected['algorithm']}.** Reason: {selected['reason']}.",
        "",
        "Hyperparameters: `"
        + ", ".join(f"{k}={v}" for k, v in selected["hyperparameters"].items())
        + "`",
        "",
    ]
    return "\n".join(lines)


def _log_to_mlflow(
    params: dict[str, Any], smoke: bool, report: dict[str, Any], duration_s: float
) -> str:
    import mlflow

    from src.models.tracking import artifact_location

    uri = tracking_uri(params, smoke)
    mlflow.set_tracking_uri(uri)
    name = experiment_name(params, smoke)
    location = artifact_location(uri)
    if location and mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(name, artifact_location=location)
    mlflow.set_experiment(name)
    selected = report["selected"]
    with mlflow.start_run(run_name=f"tune-seed{params['seed']}") as parent:
        mlflow.set_tags({**run_tags(params, smoke, selected["algorithm"]), "run_type": "tuning"})
        mlflow.log_params(
            {"n_trials": report["n_trials"], "families": ",".join(report["families"])}
        )
        mlflow.log_metric("tune_duration_s", duration_s)
        mlflow.log_metric("best_val_roc_auc", selected["val_roc_auc"])
        for family, result in report["families"].items():
            with mlflow.start_run(run_name=f"tune-{family}", nested=True):
                mlflow.set_tags({"run_type": "tuning_family", "algorithm": family})
                mlflow.log_metric("best_val_roc_auc", result["best_value"])
                if result.get("cv_roc_auc_mean") is not None:
                    mlflow.log_metric("cv_roc_auc_mean", result["cv_roc_auc_mean"])
                    mlflow.log_metric("cv_roc_auc_std", result["cv_roc_auc_std"])
                for trial in result["trials"]:
                    mlflow.log_metric("val_roc_auc", trial["val_roc_auc"], step=trial["number"])
                mlflow.log_dict(result, "family_result.json")
        mlflow.log_artifact(str(tuning_path(params, smoke)), artifact_path="reports")
        mlflow.log_artifact(str(comparison_path(params, smoke)), artifact_path="reports")
        return parent.info.run_id


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    target = params["data"]["target_column"]
    columns = model_input_columns(params)
    splits = load_splits(params, smoke)
    train, val, _test = (splits[name] for name in SPLITS)  # test is deliberately not used
    data = {
        "params": params,
        "x_train": train[columns],
        "y_train": train[target],
        "x_val": val[columns],
        "y_val": val[target],
    }

    config = load_yaml(MODEL_PARAMS_FILE)
    spaces = config.get("search_spaces") or {}
    n_trials = params["tune"]["smoke_n_trials" if smoke else "n_trials"]
    cv_folds = int(params["tune"].get("cv_folds", 0))

    started = time.perf_counter()
    families: dict[str, dict[str, Any]] = {}
    for family in params["tune"]["families"]:
        if family not in spaces:
            raise ConfigError(f"no search space for {family!r} in {MODEL_PARAMS_FILE}")
        defaults = dict(config.get(family) or {})
        _study, families[family] = _tune_family(
            family, defaults, spaces[family], data, n_trials, params["seed"], cv_folds
        )
        log.info(
            "tuned %s: best val AUC %.4f in %d trials",
            family,
            families[family]["best_value"],
            n_trials,
        )
    duration_s = time.perf_counter() - started

    winner, reason = select_winner(families, params["tune"]["simplicity_margin"])
    report = {
        "metric": "val_roc_auc",
        "seed": params["seed"],
        "smoke": smoke,
        "n_trials": n_trials,
        "cv_folds": cv_folds,
        "families": families,
        "selected": {
            "algorithm": winner,
            "hyperparameters": families[winner]["best_hyperparameters"],
            "val_roc_auc": families[winner]["best_value"],
            "reason": reason,
        },
    }
    write_json(tuning_path(params, smoke), report)
    atomic_write_bytes(comparison_path(params, smoke), comparison_markdown(report).encode("utf-8"))
    run_id = _log_to_mlflow(params, smoke, report, duration_s)
    log.info("selected %s (%s); MLflow tuning run %s", winner, reason, run_id)
    return {"selected": winner, "val_roc_auc": round(families[winner]["best_value"], 4)}


if __name__ == "__main__":
    raise SystemExit(
        stage_main(run, description="Tune model families with Optuna and pick the winner")
    )
