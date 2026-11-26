"""Weekly UCI ingestion and a manually triggerable, gated Kubeflow training workflow."""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.decorators import task
from airflow.models.param import Param
from airflow.operators.python import get_current_context
from airflow.sensors.python import PythonSensor

try:  # Airflow puts the dags folder on sys.path
    from orchestration_common import SDK, data_task, emit, execute, failed, poll, workspace
except ImportError:  # imported as a package (unit tests)
    from pipelines.airflow.dags.orchestration_common import (
        SDK,
        data_task,
        emit,
        execute,
        failed,
        poll,
        workspace,
    )


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
