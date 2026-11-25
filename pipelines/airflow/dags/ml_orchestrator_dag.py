"""Weekly UCI ingestion and a manually triggerable, gated Kubeflow training workflow."""

import hashlib
import logging
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pendulum
import requests
from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowFailException
from airflow.models.param import Param
from airflow.operators.python import get_current_context
from airflow.sensors.python import PythonSensor

LOG = logging.getLogger(__name__)
ROOT = Path("/app")
ML = "/opt/venv/bin/python"
SDK = "/opt/kfp/bin/python"


def emit(message):
    LOG.info("%s", message)
    url = os.environ.get("PHASE5_NOTIFY_WEBHOOK")
    if url:
        requests.post(url, json={"text": message}, timeout=10).raise_for_status()


def failed(context):
    ti = context["task_instance"]
    emit(f"Credit risk DAG {ti.run_id}: {ti.task_id} failed. See Airflow task logs.")


def workspace(context):
    request = hashlib.sha256(context["run_id"].encode()).hexdigest()[:32]
    return Path(os.environ.get("PHASE5_WORK", "/opt/airflow/phase5-work")) / request


def execute(python, module, *args):
    subprocess.run(  # noqa: S603
        [python, "-m", module, *map(str, args)],
        cwd=ROOT,
        check=True,
        env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(ROOT)},
    )


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


with DAG(
    dag_id="ml_orchestrator_dag",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule="@weekly",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=8),
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
        "execution_timeout": timedelta(minutes=30),
        "on_failure_callback": failed,
        "sla": timedelta(hours=6),
    },
    params={
        "source": Param("uci", enum=["uci", "s3", "synthetic", "corrupt"]),
        "strict_gate": Param(False, type="boolean"),
        "retry_demo": Param(False, type="boolean"),
    },
    tags=["credit-risk", "phase-5"],
) as dag:

    @task
    def fetch_data():
        ctx = get_current_context()
        if ctx["params"]["retry_demo"] and ctx["ti"].try_number == 1:
            raise RuntimeError("Requested one-time retry demonstration")
        data_task("fetch")

    @task
    def validate():
        data_task("validate")

    @task
    def dvc_push():
        data_task("push")

    @task
    def trigger_kfp_run():
        ctx = get_current_context()
        options = ["--strict-gate"] if ctx["params"]["strict_gate"] else []
        work = workspace(ctx)
        execute(SDK, "pipelines.kubeflow.client", "submit", work, *options)
        return (work / "kfp-run-id.txt").read_text()

    wait = PythonSensor(
        task_id="poll_run",
        python_callable=poll,
        mode="reschedule",
        poke_interval=45,
        timeout=6 * 3600,
    )

    @task
    def check_gate():
        data_task("gate")

    @task
    def notify():
        ctx = get_current_context()
        run_id = (workspace(ctx) / "kfp-run-id.txt").read_text()
        emit(
            f"Credit risk DAG {ctx['run_id']} completed. KFP run {run_id} produced a staging model."
        )

    (
        fetch_data()
        >> validate()
        >> dvc_push()
        >> trigger_kfp_run()
        >> wait
        >> check_gate()
        >> notify()
    )
