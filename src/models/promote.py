"""Promote the `challenger` model to `champion` if it beats the current champion.

    python -m src.models.promote [--params params.yaml] [--smoke]

Both models are scored on the same test split (rules.md §5) and the predefined criteria in
params.yaml `promotion` decide: ROC-AUC must improve by `min_auc_gain`, PR-AUC and KS must
not drop by more than `max_regression_drop`, and no fairness gap (approval rate, TPR, FPR, AUC
between groups of SEX, age band, MARRIAGE) may grow by more than `max_fairness_regression`.
With `fairness.enforce: true` a challenger that fails the absolute fairness checks is also rejected.
Exit 3 means "not promoted": the champion is untouched. With no champion yet, the challenger
becomes the first one (it already passed the gate when it was registered).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.common.cli import stage_main
from src.common.errors import GateFailedError, IngestionError
from src.common.io import write_json
from src.common.logging_utils import get_logger
from src.data.paths import promotion_path, split_path
from src.models.evaluate import REGRESSION_METRICS, check_regression, compute_metrics
from src.models.fairness import (
    fairness_regressions,
    fairness_report,
    load_test_frame,
    run_checks,
)
from src.models.registry import (
    alias_version,
    load_by_alias,
    mlflow_client,
    model_uri_for_alias,
    set_alias,
)
from src.models.tracking import registry_model_name

log = get_logger(__name__)


def decide(
    candidate: dict[str, float],
    champion: dict[str, float] | None,
    params: dict[str, Any],
    fairness: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Pure promotion decision from two metric dicts. `fairness` is
    {"regressions": [...], "failed_checks": [...]} as computed by `run` (None = not evaluated)."""
    rules = params["promotion"]
    reasons: list[str] = []
    gain = None
    regressions: list[str] = []
    if champion is None:
        reasons.append("no champion yet")
    else:
        gain = candidate["roc_auc"] - champion["roc_auc"]
        if gain < rules["min_auc_gain"] - 1e-12:
            reasons.append(
                f"ROC-AUC gain {gain:+.4f} is below the required {rules['min_auc_gain']:+.4f}"
            )
        regressions = check_regression(candidate, champion, rules["max_regression_drop"])
        if regressions:
            reasons.append(
                f"regressed by more than {rules['max_regression_drop']:g} on: "
                f"{', '.join(regressions)}"
            )
    if rules["fairness_check"] and fairness is not None:
        if fairness.get("regressions"):
            reasons.append(
                f"fairness regression versus champion on: {', '.join(fairness['regressions'])}"
            )
        if fairness.get("failed_checks"):
            reasons.append(f"fairness checks failed: {', '.join(fairness['failed_checks'])}")
    return {
        "promote": not [r for r in reasons if r != "no champion yet"],
        "reasons": reasons,
        "roc_auc_gain": gain,
        "regressed_metrics": regressions,
        "criteria": {
            k: rules[k]
            for k in (
                "min_auc_gain",
                "max_regression_drop",
                "fairness_check",
                "max_fairness_regression",
            )
        },
    }


def _score(
    params: dict[str, Any], alias: str, test: pd.DataFrame, smoke: bool
) -> tuple[dict[str, float], dict[str, Any]]:
    """(metrics, fairness report) of the model behind `alias` on the shared test split."""
    import mlflow

    model = load_by_alias(params, alias, smoke)
    signature = mlflow.models.get_model_info(model_uri_for_alias(params, alias, smoke)).signature
    columns = signature.inputs.input_names()
    probabilities = model.predict_proba(test[columns].astype("float64"))[:, 1]
    metrics = compute_metrics(
        test[params["data"]["target_column"]],
        probabilities,
        params["evaluate"]["decision_threshold"],
    )
    return metrics, fairness_report(test, probabilities, params)


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    challenger_alias = params["mlflow"]["challenger_alias"]
    champion_alias = params["mlflow"]["champion_alias"]
    name = registry_model_name(params, smoke)

    challenger = alias_version(params, challenger_alias, smoke)
    if challenger is None:
        raise IngestionError(
            f"no '{challenger_alias}' on {name!r}: run `python -m src.models.registry` first"
        )
    champion = alias_version(params, champion_alias, smoke)
    if champion is not None and champion.version == challenger.version:
        raise IngestionError(f"version {challenger.version} is already the champion")

    test_file = split_path(params, "test", smoke)
    if not test_file.is_file():
        raise IngestionError(f"{test_file} not found (run the preprocess stage first)")
    test = load_test_frame(params, smoke)

    candidate_metrics, candidate_fairness = _score(params, challenger_alias, test, smoke)
    champion_metrics, champion_fairness = (
        _score(params, champion_alias, test, smoke) if champion is not None else (None, None)
    )
    tolerance = params["promotion"]["max_fairness_regression"]
    fairness = {
        "regressions": (
            fairness_regressions(candidate_fairness, champion_fairness, tolerance)
            if champion_fairness is not None
            else []
        ),
        "failed_checks": (
            [
                f"{c['attribute']}.{c['check']}"
                for c in run_checks(candidate_fairness, params)
                if c["status"] == "failed"
            ]
            if params["fairness"]["enforce"]
            else []
        ),
    }
    decision = decide(candidate_metrics, champion_metrics, params, fairness)
    decision.update(
        {
            "model_name": name,
            "challenger_version": str(challenger.version),
            "champion_version": None if champion is None else str(champion.version),
            "candidate_metrics": {m: candidate_metrics[m] for m in REGRESSION_METRICS},
            "champion_metrics": (
                None
                if champion_metrics is None
                else {m: champion_metrics[m] for m in REGRESSION_METRICS}
            ),
            "fairness": fairness,
            "smoke": smoke,
        }
    )
    write_json(promotion_path(params, smoke), decision)

    client = mlflow_client(params, smoke)
    note = "promoted" if decision["promote"] else "rejected"
    client.set_model_version_tag(name, challenger.version, "promotion_decision", note)
    if not decision["promote"]:
        raise GateFailedError("challenger not promoted: " + "; ".join(decision["reasons"]))

    set_alias(params, champion_alias, challenger.version, smoke)
    client.delete_registered_model_alias(name, challenger_alias)
    log.info(
        "version %s is now '%s' (%s)",
        challenger.version,
        champion_alias,
        "; ".join(decision["reasons"]) or "beat champion",
    )
    return {"champion_version": str(challenger.version), "roc_auc_gain": decision["roc_auc_gain"]}


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Promote the challenger to champion if it wins"))
