"""Challenger-versus-champion decision for the retrain DAG. It RECOMMENDS; it never moves `champion`.

    python -m pipelines.retrain.decision <work-dir> <uci|s3|synthetic|corrupt>

Runs with the ML interpreter after the Kubeflow run. The model the pipeline just registered (alias `staging`, verified
against this run's request id by `pipelines.phase5.result`) is compared with the current champion on the test metrics
MLflow recorded for each run (same seed and test size, so the same holdout) and judged by the predefined rules in params.yaml `promotion` (ROC-AUC gain, PR-AUC/KS regression, fairness), using
the pure `src.models.promote.decide`. The outcome is written to `<work>/decision.json` and as MLflow tags:

* recommended -> the version gets the alias `challenger` and the tag `promotion_decision=recommended`; a human approves
  it by running the promote workflow (docs/promotion-rollback.md).
* rejected    -> the tag `promotion_decision=rejected` and the reasons are recorded; nothing else changes.

Exit codes follow docs/contracts-s1.md: 0 decision recorded (either way), 1 error, 3 quality gate rejected the training run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

GATE_REJECTED = 3


def recorded_metrics(client, run_id):
    """The test metrics MLflow recorded for a run, keyed like `src.models.evaluate.compute_metrics`."""
    run = client.get_run(run_id)
    metrics = {
        k.removeprefix("test_"): v for k, v in run.data.metrics.items() if k.startswith("test_")
    }
    return metrics, run.data.tags.get("seed"), metrics.get("n")


def decide_for_request(request: str, smoke: bool) -> dict[str, Any]:
    from src.common.config import load_params
    from src.models import promote
    from src.models.evaluate import REGRESSION_METRICS
    from src.models.registry import alias_version, mlflow_client
    from src.models.tracking import registry_model_name

    from pipelines.phase5.result import check_result

    check_result(
        request, smoke
    )  # one evaluated run, gate passed, model version tagged (GateRejected otherwise)
    params = load_params()
    name = registry_model_name(params, smoke)
    challenger = alias_version(params, "staging", smoke)  # where the Phase 5 pipeline registers
    champion = alias_version(params, params["mlflow"]["champion_alias"], smoke)
    if challenger is None:
        raise RuntimeError(f"no 'staging' version on {name!r} after a passing run")
    if champion is not None and champion.version == challenger.version:
        raise RuntimeError(f"version {challenger.version} is already the champion")

    client = mlflow_client(params, smoke)
    candidate_metrics, candidate_seed, candidate_n = recorded_metrics(client, challenger.run_id)
    champion_metrics = None
    if champion is not None:
        champion_metrics, champion_seed, champion_n = recorded_metrics(client, champion.run_id)
        # Both models were evaluated on the split that the same seed and data produce, so the recorded metrics
        # are a same-holdout comparison. Refuse to judge if that cannot be established.
        if (candidate_seed, candidate_n) != (champion_seed, champion_n):
            raise RuntimeError(
                f"the runs are not comparable (seed/test size {candidate_seed}/{candidate_n} vs {champion_seed}/{champion_n})"
            )
    decision = promote.decide(candidate_metrics, champion_metrics, params, fairness=None)
    decision["reasons"].append(
        "fairness gaps were not re-evaluated in the DAG (the test split lives in the Kubeflow pods): "
        "check reports/fairness.json for the candidate before approving"
    )
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
            "fairness": {"evaluated": False},
            "comparison": "recorded test metrics of both MLflow runs (same seed and test size)",
            "request": request,
            "smoke": smoke,
        }
    )
    client.set_model_version_tag(
        name,
        challenger.version,
        "promotion_decision",
        "recommended" if decision["promote"] else "rejected",
    )
    if decision["promote"]:
        client.set_registered_model_alias(name, "challenger", challenger.version)
    return decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("mode", choices=["uci", "s3", "synthetic", "corrupt"])
    args = parser.parse_args()
    from pipelines.phase5.result import GateRejected

    try:
        decision = decide_for_request(args.work.name, args.mode == "synthetic")
    except GateRejected as error:
        print(f"GateRejected: {error}", file=sys.stderr)
        sys.exit(GATE_REJECTED)
    (args.work / "decision.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    verdict = "RECOMMENDED for approval" if decision["promote"] else "REJECTED"
    print(
        f"Challenger v{decision['challenger_version']} vs champion v{decision['champion_version']}: {verdict}. "
        + ("; ".join(decision["reasons"]) or f"ROC-AUC gain {decision['roc_auc_gain']:+.4f}")
    )


if __name__ == "__main__":
    main()
