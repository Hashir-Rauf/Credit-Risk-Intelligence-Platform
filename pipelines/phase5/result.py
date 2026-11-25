"""Require a gate decision and a concrete registered version belonging to this DAG run."""

import re

from mlflow.tracking import MlflowClient
from src.common.config import load_params
from src.models.tracking import experiment_name, registry_model_name


def check_result(request, smoke):
    if not re.fullmatch(r"[0-9a-f]{32}", request):
        raise ValueError("Invalid orchestration request ID")
    params = load_params()
    client = MlflowClient()
    experiment = client.get_experiment_by_name(experiment_name(params, smoke))
    if not experiment:
        raise RuntimeError("No matching MLflow experiment")
    runs = client.search_runs([experiment.experiment_id], f"tags.phase5_request = '{request}'")
    if len(runs) != 1:
        raise RuntimeError(f"Expected one evaluated training run, found {len(runs)}")
    run = runs[0]
    tags = run.data.tags
    if tags.get("gate_passed") != "true":
        raise RuntimeError(f"Quality gate rejected training run {run.info.run_id}")
    version = client.get_model_version(
        registry_model_name(params, smoke), tags["phase5_staging_version"]
    )
    if version.tags.get("source_run_id") != run.info.run_id or not version.tags.get(
        "phase5_oci_digest"
    ):
        raise RuntimeError("Model version provenance does not match the evaluated training run")
    print(f"Gate passed. MLflow run {run.info.run_id}, model {version.name} v{version.version}")
