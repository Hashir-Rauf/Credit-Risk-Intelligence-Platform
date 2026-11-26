"""Helpers shared by the Airflow DAGs (defines no DAG, so importing it never registers one)."""

import hashlib
import logging
import os
import subprocess
from pathlib import Path

import requests
from airflow.exceptions import AirflowFailException
from airflow.operators.python import get_current_context

LOG = logging.getLogger(__name__)
ROOT = Path("/app")
ML = "/opt/venv/bin/python"
SDK = "/opt/kfp/bin/python"
VALIDATION_FAILED = 2  # exit codes of airflow_data, shared with the src/ stages
GATE_REJECTED = 3


def emit(message):
    LOG.info("%s", message)
    url = os.environ.get("PHASE5_NOTIFY_WEBHOOK")
    if url:
        requests.post(url, json={"text": message}, timeout=10).raise_for_status()


def failed(context):
    ti = context["task_instance"]
    emit(f"Credit risk DAG {ti.run_id}: {ti.task_id} failed. See Airflow task logs.")


def request_id(run_id, started):
    """Identify one orchestration run across Airflow, Kubeflow and MLflow.

    A scheduled run's `run_id` (scheduled__<date>) is the same on every Airflow installation, so
    two installations sharing one MLflow server would collide and the gate check could not tell
    their runs apart (found in the first live acceptance run). The DagRun's start time is fixed
    for the life of a run (retries reuse it) but differs between installations: stable per run,
    unique per installation.
    """
    identity = f"{run_id}|{started.isoformat() if started else ''}"
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def workspace(context):
    request = request_id(context["run_id"], context["dag_run"].start_date)
    return Path(os.environ.get("PHASE5_WORK", "/opt/airflow/phase5-work")) / request


def execute(python, module, *args):
    result = subprocess.run(  # noqa: S603
        [python, "-m", module, *map(str, args)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(ROOT)},
    )
    # Show the helper's own output in the task log (a failing helper's error used to be invisible).
    for line in (result.stdout + result.stderr).splitlines():
        LOG.info("[%s] %s", module.rsplit(".", 1)[-1], line)
    if result.returncode in (VALIDATION_FAILED, GATE_REJECTED):
        # Both are deterministic outcomes: retrying cannot change them, so fail now.
        reason = (
            "data validation failed"
            if result.returncode == VALIDATION_FAILED
            else "quality gate rejected"
        )
        raise AirflowFailException(f"{module} {args[0]}: {reason}; not retrying")
    result.check_returncode()


def data_task(action):
    context = get_current_context()
    execute(
        ML, "pipelines.phase5.airflow_data", action, workspace(context), context["params"]["source"]
    )


def poll(**context):
    work = workspace(context)
    execute(SDK, "pipelines.kubeflow.client", "poll", work)
    state = (work / "kfp-state.txt").read_text().upper()
    if state in {"FAILED", "CANCELED", "CANCELLED", "SKIPPED"}:
        raise AirflowFailException(f"Kubeflow run {state}; see {work / 'kfp-run-id.txt'}")
    return state == "SUCCEEDED"
