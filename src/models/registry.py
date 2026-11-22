"""MLflow Model Registry helpers: register the evaluated model and manage aliases.

    python -m src.models.registry [--params params.yaml] [--smoke]

The command registers the latest trained model (reports/last_run.json) as a new version of
`mlflow.registry_model_name` and points the `challenger` alias at it. It refuses models that
failed the gate (exit 3). Promotion to `champion` is a separate, deliberate step
(`python -m src.models.promote`). MLflow stages are deprecated, so only aliases are used;
serving loads `models:/<name>@champion`.
"""

from __future__ import annotations

import contextlib
from typing import Any

from src.common.cli import stage_main
from src.common.errors import GateFailedError, IngestionError, PipelineError
from src.common.io import read_json, write_json
from src.common.logging_utils import get_logger
from src.data.paths import evaluation_path, last_run_path, metrics_path, registration_path
from src.models.evaluate import check_gate
from src.models.tracking import registry_model_name, tracking_uri

log = get_logger(__name__)

SOURCE_RUN_TAG = "source_run_id"


def passes_gate(metrics: dict[str, float], params: dict[str, Any]) -> bool:
    """True if `metrics` (needs `roc_auc`) meet the quality gate in params.yaml."""
    return bool(check_gate(metrics, params)["passed"])


def mlflow_client(params: dict[str, Any], smoke: bool):
    import mlflow
    from mlflow.tracking import MlflowClient

    uri = tracking_uri(params, smoke)
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    return MlflowClient(tracking_uri=uri, registry_uri=uri)


def alias_version(params: dict[str, Any], alias: str, smoke: bool = False):
    """The ModelVersion that `alias` points at, or None if the alias (or model) does not exist."""
    from mlflow.exceptions import MlflowException

    try:
        return mlflow_client(params, smoke).get_model_version_by_alias(
            registry_model_name(params, smoke), alias
        )
    except MlflowException:
        return None


def model_uri_for_alias(params: dict[str, Any], alias: str, smoke: bool = False) -> str:
    return f"models:/{registry_model_name(params, smoke)}@{alias}"


def load_by_alias(params: dict[str, Any], alias: str, smoke: bool = False) -> Any:
    """Load the sklearn pipeline behind an alias (e.g. 'champion'). Raises if it is not set."""
    import mlflow

    if alias_version(params, alias, smoke) is None:
        raise IngestionError(
            f"alias {alias!r} is not set on {registry_model_name(params, smoke)!r}"
        )
    return mlflow.sklearn.load_model(model_uri_for_alias(params, alias, smoke))


def set_alias(params: dict[str, Any], alias: str, version: str | int, smoke: bool = False) -> None:
    mlflow_client(params, smoke).set_registered_model_alias(
        registry_model_name(params, smoke), alias, str(version)
    )


def _existing_version(client: Any, name: str, run_id: str):
    for version in client.search_model_versions(f"name='{name}'"):
        if version.tags.get(SOURCE_RUN_TAG) == run_id:
            return version
    return None


def _evaluated_candidate(
    params: dict[str, Any], smoke: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(evaluation report, last-run info), checked to belong together and to have passed."""
    for needed in (
        evaluation_path(params, smoke),
        last_run_path(params, smoke),
        metrics_path(params, smoke),
    ):
        if not needed.is_file():
            raise IngestionError(f"{needed} not found (run train and evaluate first)")
    evaluation = read_json(evaluation_path(params, smoke))
    trained = read_json(metrics_path(params, smoke))["test"]["roc_auc"]
    if abs(evaluation["test_metrics"]["roc_auc"] - trained) > params["evaluate"]["repro_tolerance"]:
        raise PipelineError(
            "evaluation.json is stale: it does not match the latest training run; rerun evaluate"
        )
    if not evaluation["gate"]["passed"]:
        raise GateFailedError("the model failed the quality gate and cannot be registered")
    return evaluation, read_json(last_run_path(params, smoke))


def register_candidate(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    """Register the evaluated model (idempotent per training run) and set the challenger alias."""
    import mlflow
    from mlflow.exceptions import MlflowException

    evaluation, last_run = _evaluated_candidate(params, smoke)
    client = mlflow_client(params, smoke)
    name = registry_model_name(params, smoke)
    alias = params["mlflow"]["challenger_alias"]

    with contextlib.suppress(MlflowException):  # already exists
        client.create_registered_model(name)

    version = _existing_version(client, name, last_run["run_id"])
    if version is None:
        version = mlflow.register_model(last_run["model_uri"], name)
        run_tags = client.get_run(last_run["run_id"]).data.tags
        metrics = evaluation["test_metrics"]
        tags = {
            SOURCE_RUN_TAG: last_run["run_id"],
            "gate_passed": "true",
            "test_roc_auc": f"{metrics['roc_auc']:.6f}",
            "test_pr_auc": f"{metrics['pr_auc']:.6f}",
            "test_ks": f"{metrics['ks']:.6f}",
            "algorithm": run_tags.get("algorithm", "unknown"),
            "git_commit_sha": run_tags.get("git_commit_sha", "unknown"),
            "dvc_data_hash": run_tags.get("dvc_data_hash", "unknown"),
            "data_sha256": run_tags.get("data_sha256", "unknown"),
            "smoke": str(smoke).lower(),
        }
        for key, value in tags.items():
            client.set_model_version_tag(name, version.version, key, value)
    client.set_registered_model_alias(name, alias, str(version.version))

    record = {
        "model_name": name,
        "version": str(version.version),
        "alias": alias,
        "run_id": last_run["run_id"],
        "test_roc_auc": evaluation["test_metrics"]["roc_auc"],
        "smoke": smoke,
    }
    write_json(registration_path(params, smoke), record)
    log.info("registered %s version %s as '%s'", name, version.version, alias)
    return record


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    record = register_candidate(params, smoke)
    return {"model": record["model_name"], "version": record["version"], "alias": record["alias"]}


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Register the evaluated model as the challenger"))
