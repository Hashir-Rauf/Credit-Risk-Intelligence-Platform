"""Group fairness analysis of the trained model on the test split.

    python -m src.models.fairness [--params params.yaml] [--smoke]

The model never sees SEX, MARRIAGE or AGE (rules.md section 2), so this checks the outcome
anyway: for each attribute and group it reports the default rate, the approval rate (predicted
non-default at the decision threshold), recall (TPR), false-positive rate and ROC-AUC, and the
gaps between groups. Excluding an attribute does not remove proxies for it, which is why the
outcome is measured and not just the inputs.

`fairness.enforce: false` (default) reports findings without failing the stage, so a person
decides what is acceptable after seeing real numbers; `true` makes failed checks exit 3.
The optional ablation fits the same model WITH the sensitive inputs, to document the choice
to exclude them with evidence.
"""

from __future__ import annotations

from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from src.common.cli import stage_main
from src.common.errors import ConfigError, GateFailedError, IngestionError
from src.common.io import read_json, write_json
from src.common.logging_utils import get_logger
from src.data.paths import fairness_path, metrics_path, model_path, split_path
from src.features.feast_features import attach_feast_features
from src.features.preprocessing import build_pipeline, model_input_columns
from src.models.train import build_estimator, resolve_model_choice

log = get_logger(__name__)

GAP_METRICS = ("approval_rate_gap", "tpr_gap", "fpr_gap", "auc_gap")
SOURCE_COLUMNS = {"SEX": "SEX", "MARRIAGE": "MARRIAGE", "EDUCATION": "EDUCATION", "AGE_BAND": "AGE"}


def age_band(ages: Any, edges: list[float]) -> pd.Series:
    """Labels like '<30', '30-39', '60+' for `edges` [30, 40, 50, 60]."""
    ages = pd.Series(np.asarray(ages, dtype=float))
    labels = [f"<{edges[0]:g}"]
    labels += [f"{lo:g}-{hi - 1:g}" for lo, hi in zip(edges[:-1], edges[1:], strict=True)]
    labels.append(f"{edges[-1]:g}+")
    bins = [-np.inf, *edges, np.inf]
    return pd.cut(ages, bins=bins, labels=labels, right=False).astype(str)


def group_metrics(y_true: Any, y_prob: Any, threshold: float) -> dict[str, Any]:
    """Outcome metrics of one group. Undefined values (no positives/negatives) are None."""
    y = np.asarray(y_true).astype(int)
    p = np.asarray(y_prob, dtype=float)
    flagged = p >= threshold  # predicted to default = declined
    positives, negatives = (y == 1).sum(), (y == 0).sum()
    both = positives > 0 and negatives > 0
    return {
        "n": int(y.size),
        "default_rate": float(y.mean()),
        "approval_rate": float(1 - flagged.mean()),
        "tpr": float(flagged[y == 1].mean()) if positives else None,
        "fpr": float(flagged[y == 0].mean()) if negatives else None,
        "precision": float(y[flagged].mean()) if flagged.any() else None,
        "auc": float(roc_auc_score(y, p)) if both else None,
    }


def _spread(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return float(max(present) - min(present)) if len(present) >= 2 else None


def disparities(groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Gaps between the groups of one attribute (groups must already be size-filtered)."""
    rates = [g["approval_rate"] for g in groups.values()]
    highest = max(rates) if rates else 0.0
    return {
        "n_groups": len(groups),
        "approval_rate_gap": _spread(rates),
        "disparate_impact": (
            float(min(rates) / highest) if len(rates) >= 2 and highest > 0 else None
        ),
        "tpr_gap": _spread([g["tpr"] for g in groups.values()]),
        "fpr_gap": _spread([g["fpr"] for g in groups.values()]),
        "auc_gap": _spread([g["auc"] for g in groups.values()]),
    }


def attribute_report(
    labels: pd.Series, y_true: Any, y_prob: Any, threshold: float, min_size: int
) -> dict[str, Any]:
    labels = pd.Series(np.asarray(labels)).astype(str)
    y = np.asarray(y_true)
    p = np.asarray(y_prob)
    groups, small = {}, {}
    for label in sorted(labels.unique()):
        mask = (labels == label).to_numpy()
        if mask.sum() < min_size:
            small[label] = int(mask.sum())
            continue
        groups[label] = group_metrics(y[mask], p[mask], threshold)
    return {"groups": groups, "excluded_small_groups": small, "disparities": disparities(groups)}


def attribute_labels(frame: pd.DataFrame, attribute: str, params: dict[str, Any]) -> pd.Series:
    source = SOURCE_COLUMNS.get(attribute)
    if source is None or source not in frame.columns:
        raise ConfigError(f"cannot build fairness groups for {attribute!r}: column missing")
    if attribute == "AGE_BAND":
        return age_band(frame["AGE"], params["fairness"]["age_band_edges"])
    return frame[source].astype(int).astype(str)


def fairness_report(
    frame: pd.DataFrame, y_prob: Any, params: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """{attribute: attribute_report} for the attributes configured in params.fairness."""
    threshold = params["evaluate"]["decision_threshold"]
    target = frame[params["data"]["target_column"]]
    return {
        attribute: attribute_report(
            attribute_labels(frame, attribute, params),
            target,
            y_prob,
            threshold,
            params["fairness"]["min_group_size"],
        )
        for attribute in params["fairness"]["attributes"]
    }


def run_checks(report: dict[str, dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Threshold checks per attribute. Gaps that cannot be computed (fewer than two groups
    with enough rows) are reported as skipped, not as passed."""
    rules = params["fairness"]
    checks: list[dict[str, Any]] = []
    for attribute, result in report.items():
        d = result["disparities"]
        for name, value, passed in (
            (
                "disparate_impact",
                d["disparate_impact"],
                (
                    None
                    if d["disparate_impact"] is None
                    else d["disparate_impact"] >= rules["min_disparate_impact"]
                ),
            ),
            (
                "tpr_gap",
                d["tpr_gap"],
                None if d["tpr_gap"] is None else d["tpr_gap"] <= rules["max_tpr_gap"],
            ),
            (
                "auc_gap",
                d["auc_gap"],
                None if d["auc_gap"] is None else d["auc_gap"] <= rules["max_auc_gap"],
            ),
        ):
            limit = (
                rules["min_disparate_impact"]
                if name == "disparate_impact"
                else rules[f"max_{name}"]
            )
            checks.append(
                {
                    "attribute": attribute,
                    "check": name,
                    "value": value,
                    "limit": limit,
                    "status": "skipped" if passed is None else ("passed" if passed else "failed"),
                }
            )
    return checks


def fairness_regressions(
    candidate: dict[str, dict[str, Any]], champion: dict[str, dict[str, Any]], tolerance: float
) -> list[str]:
    """'<attribute>.<gap>' for every gap that grew by more than `tolerance` compared with the
    champion (rules.md section 5: no fairness regression)."""
    found = []
    for attribute, result in candidate.items():
        if attribute not in champion:
            continue
        for gap in GAP_METRICS:
            new, old = result["disparities"][gap], champion[attribute]["disparities"][gap]
            if new is not None and old is not None and new - old > tolerance + 1e-12:
                found.append(f"{attribute}.{gap}")
    return found


def ablation(
    params: dict[str, Any], smoke: bool, frames: dict[str, pd.DataFrame]
) -> dict[str, Any]:
    """Same algorithm and hyperparameters fitted WITH the sensitive inputs, scored on test."""
    algorithm, hyperparameters, _ = resolve_model_choice(params, smoke)
    target = params["data"]["target_column"]
    train, test = frames["train"], frames["test"]
    variants = {}
    for name, exclude in (("without_sensitive_inputs", True), ("with_sensitive_inputs", False)):
        variant = {
            **params,
            "features": {**params["features"], "exclude_sensitive_from_model": exclude},
        }
        columns = model_input_columns(variant)
        pipeline = build_pipeline(
            variant, build_estimator(algorithm, hyperparameters, params["seed"])
        )
        pipeline.fit(train[columns], train[target])
        probabilities = pipeline.predict_proba(test[columns])[:, 1]
        report = fairness_report(test, probabilities, params)
        variants[name] = {
            "test_roc_auc": float(roc_auc_score(test[target], probabilities)),
            "disparities": {a: r["disparities"] for a, r in report.items()},
        }
    gain = (
        variants["with_sensitive_inputs"]["test_roc_auc"]
        - variants["without_sensitive_inputs"]["test_roc_auc"]
    )
    minimum = params["fairness"]["ablation_min_auc_gain"]
    conclusion = f"Adding SEX, MARRIAGE and AGE changes test ROC-AUC by {gain:+.4f}. " + (
        "That is below the " f"{minimum:g} threshold, so excluding them costs nothing measurable."
        if gain < minimum
        else f"That is above the {minimum:g} threshold; they stay excluded by policy "
        "(rules.md section 2), not because it is free, and the accuracy cost is accepted."
    )
    return {
        "algorithm": algorithm,
        **variants,
        "auc_gain_from_sensitive_inputs": float(gain),
        "conclusion": conclusion,
    }


def load_test_frame(params: dict[str, Any], smoke: bool) -> pd.DataFrame:
    path = split_path(params, "test", smoke)
    if not path.is_file():
        raise IngestionError(f"{path} not found (run the preprocess stage first)")
    return attach_feast_features({"test": pd.read_parquet(path)}, params, smoke)["test"]


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    for needed in (model_path(params, smoke), metrics_path(params, smoke)):
        if not needed.is_file():
            raise IngestionError(f"{needed} not found (run train first)")
    model = joblib.load(model_path(params, smoke))  # trusted, locally produced artifact only
    test = load_test_frame(params, smoke)
    columns = model_input_columns(params)
    probabilities = model.predict_proba(test[columns])[:, 1]

    report = fairness_report(test, probabilities, params)
    checks = run_checks(report, params)
    failed = [f"{c['attribute']}.{c['check']}" for c in checks if c["status"] == "failed"]
    result: dict[str, Any] = {
        "algorithm": read_json(metrics_path(params, smoke))["algorithm"],
        "smoke": smoke,
        "threshold": params["evaluate"]["decision_threshold"],
        "sensitive_attributes_in_model": not params["features"]["exclude_sensitive_from_model"],
        "min_group_size": params["fairness"]["min_group_size"],
        "enforced": params["fairness"]["enforce"],
        "attributes": report,
        "checks": checks,
        "failed_checks": failed,
        "passed": not failed,
        "notes": [
            "Approval = predicted non-default at the decision threshold; tpr/fpr are rates of "
            "being flagged as a likely defaulter among actual defaulters / non-defaulters.",
            "Group rates are estimates on the test split; small groups are excluded from gaps.",
            "Excluding a sensitive input does not remove proxies for it; "
            "this report measures outcomes.",
        ],
    }
    if params["fairness"]["run_ablation"]:
        frames = {
            "train": attach_feast_features(
                {"train": pd.read_parquet(split_path(params, "train", smoke))}, params, smoke
            )["train"],
            "test": test,
        }
        result["ablation"] = ablation(params, smoke, frames)
    write_json(fairness_path(params, smoke), result)
    log.info("fairness: %d attributes, %d checks failed %s", len(report), len(failed), failed)
    if failed and params["fairness"]["enforce"]:
        raise GateFailedError(f"fairness checks failed: {failed}")
    return {
        "attributes": list(report),
        "failed_checks": failed,
        "report": str(fairness_path(params, smoke)),
    }


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Group fairness analysis on the test split"))
