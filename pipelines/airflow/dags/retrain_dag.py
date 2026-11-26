"""Controlled retraining: revalidate data, train through Kubeflow, compare with the champion, request approval.

Triggers
* weekly schedule (reason=schedule)
* `drift_watch_dag`, which polls the drift monitor and starts this DAG when drift or a live-performance drop is
  detected (reason=drift or performance)
* manually from the UI (reason=manual)

Flow: fetch_data -> validate -> dvc_push -> trigger_kfp_run -> poll_run -> decide -> branch
  recommended -> request_approval   (challenger alias set; a human runs the promote workflow)
  rejected    -> record_rejection   (nothing else changes; the champion keeps serving)

This DAG never moves the `champion` alias. Promotion and rollback are explicit, approved steps
(docs/promotion-rollback.md).
"""

import json
import logging
import os
from datetime import timedelta

import pendulum
import requests
from airflow import DAG
from airflow.decorators import task
from airflow.models.param import Param
from airflow.operators.python import get_current_context
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sensors.python import PythonSensor

try:  # Airflow puts the dags folder on sys.path
    from orchestration_common import ML, SDK, data_task, emit, execute, failed, poll, workspace
except ImportError:  # imported as a package (unit tests)
    from pipelines.airflow.dags.orchestration_common import (
        ML,
        SDK,
        data_task,
        emit,
        execute,
        failed,
        poll,
        workspace,
    )

LOG = logging.getLogger(__name__)
DRIFT_STATUS_URL = os.environ.get("DRIFT_STATUS_URL", "http://host.docker.internal:9101/status")
LIVE_AUC_FLOOR = float(os.environ.get("RETRAIN_LIVE_AUC_FLOOR", "0.70"))
MIN_LABELLED_ROWS = int(os.environ.get("RETRAIN_MIN_LABELLED_ROWS", "100"))
COOLDOWN_HOURS = float(os.environ.get("RETRAIN_COOLDOWN_HOURS", "6"))


def drift_reason(status):
    """Why the monitor's status justifies a retrain, or None. Pure, so it is unit-tested."""
    if not status or status.get("state") in (None, "starting", "insufficient_data"):
        return None
    if status.get("drift_detected"):
        return "drift"
    performance = status.get("performance") or {}
    auc = performance.get("auc")
    if (
        auc is not None
        and performance.get("labelled_rows", 0) >= MIN_LABELLED_ROWS
        and auc < LIVE_AUC_FLOOR
    ):
        return "performance"
    return None


def in_cooldown(start_times, now, hours):
    """True when a retrain started less than `hours` ago (any outcome except a failed run)."""
    return any((now - started).total_seconds() < hours * 3600 for started in start_times if started)


def read_decision(work):
    return json.loads((work / "decision.json").read_text(encoding="utf-8"))


default_args = {
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=30),
    "on_failure_callback": failed,
}

with DAG(
    dag_id="retrain_dag",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule="@weekly",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=8),
    default_args=default_args,
    params={
        "source": Param("uci", enum=["uci", "s3", "synthetic", "corrupt"]),
        "strict_gate": Param(False, type="boolean"),
        "reason": Param("schedule", enum=["schedule", "drift", "performance", "manual"]),
    },
    tags=["credit-risk", "phase-8"],
) as dag:

    @task
    def fetch_data():
        ctx = get_current_context()
        emit(f"Retrain run {ctx['run_id']} started (reason: {ctx['params']['reason']}).")
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
    def decide():
        ctx = get_current_context()
        work = workspace(ctx)
        execute(ML, "pipelines.retrain.decision", work, ctx["params"]["source"])
        return read_decision(work)["promote"]

    @task.branch
    def route(recommended):
        return "request_approval" if recommended else "record_rejection"

    @task
    def request_approval():
        ctx = get_current_context()
        decision = read_decision(workspace(ctx))
        emit(
            f"Challenger v{decision['challenger_version']} beat champion v{decision['champion_version']} "
            f"(ROC-AUC {decision['roc_auc_gain']:+.4f}). Approval needed: run the 'Promote model' workflow with "
            f"version={decision['challenger_version']} (GitHub Environment 'production' asks a reviewer)."
        )

    @task
    def record_rejection():
        ctx = get_current_context()
        decision = read_decision(workspace(ctx))
        emit(
            f"Challenger v{decision['challenger_version']} REJECTED, champion v{decision['champion_version']} keeps "
            f"serving: {'; '.join(decision['reasons'])}"
        )

    recommended = fetch_data() >> validate() >> dvc_push() >> trigger_kfp_run() >> wait >> decide()
    choice = route(recommended)
    choice >> [request_approval(), record_rejection()]


with DAG(
    dag_id="drift_watch_dag",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule="*/10 * * * *",
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0, "on_failure_callback": failed},
    tags=["credit-risk", "phase-8"],
) as watch:

    def recent_retrain():
        """A retrain that is active, finished OK or is still cooling down blocks a new trigger."""
        from airflow.models import DagRun
        from airflow.utils import timezone

        runs = DagRun.find(dag_id="retrain_dag")
        return in_cooldown(
            [r.start_date for r in runs if r.state != "failed"], timezone.utcnow(), COOLDOWN_HOURS
        )

    def monitor_wants_retrain(**context):
        """True (and the reason stored in XCom) when the drift monitor reports drift or a performance drop."""
        try:
            status = requests.get(DRIFT_STATUS_URL, timeout=10).json()
        except (requests.RequestException, ValueError):
            LOG.warning("drift monitor unreachable at %s", DRIFT_STATUS_URL)
            return False
        reason = drift_reason(status)
        if reason and recent_retrain():
            LOG.info(
                "drift persists (%s) but a retrain started within %sh: not triggering another",
                reason,
                COOLDOWN_HOURS,
            )
            return False
        if reason:
            context["ti"].xcom_push(key="reason", value=reason)
            LOG.info(
                "retrain trigger: %s (%s)",
                reason,
                {k: status.get(k) for k in ("state", "drift_share")},
            )
        return bool(reason)

    breach = PythonSensor(
        task_id="check_drift_metric",
        python_callable=monitor_wants_retrain,
        mode="reschedule",
        poke_interval=60,
        timeout=9 * 60,
        soft_fail=True,  # no breach in this window is a normal outcome: skip, do not fail
    )
    start = TriggerDagRunOperator(
        task_id="trigger_retrain",
        trigger_dag_id="retrain_dag",
        conf={
            "source": "uci",
            "reason": "{{ ti.xcom_pull(task_ids='check_drift_metric', key='reason') }}",
        },
        reset_dag_run=False,
        wait_for_completion=False,
    )
    breach >> start
