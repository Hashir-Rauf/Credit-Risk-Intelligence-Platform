"""Run under the isolated Airflow interpreter (like test_dag.py)."""

import sys
from datetime import UTC
from pathlib import Path

import pytest
from airflow.models import DagBag
from pipelines.airflow.dags import ml_orchestrator_dag

DAGS = Path(ml_orchestrator_dag.__file__).parent
sys.path.insert(
    0, str(DAGS)
)  # retrain_dag imports its sibling module, as Airflow's dags folder allows
from pipelines.airflow.dags import retrain_dag as module  # noqa: E402


def test_retrain_dag_structure():
    bag = DagBag(str(DAGS), include_examples=False)
    assert not bag.import_errors
    dag = bag.dags["retrain_dag"]
    chain = ["fetch_data", "validate", "dvc_push", "trigger_kfp_run", "poll_run", "decide", "route"]
    for first, second in zip(chain, chain[1:], strict=False):
        assert dag.get_task(first).downstream_task_ids == {second}
    assert dag.get_task("route").downstream_task_ids == {"request_approval", "record_rejection"}
    assert dag.max_active_runs == 1 and dag.schedule_interval == "@weekly"
    # No task of this DAG may move the champion alias: promotion is an approved, separate step.
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "set_registered_model_alias" not in source and "set_alias" not in source


def test_drift_watch_triggers_the_retrain_dag():
    dag = DagBag(str(DAGS), include_examples=False).dags["drift_watch_dag"]
    trigger = dag.get_task("trigger_retrain")
    assert trigger.trigger_dag_id == "retrain_dag"
    assert dag.get_task("check_drift_metric").downstream_task_ids == {"trigger_retrain"}


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (None, None),
        ({"state": "insufficient_data", "drift_detected": False}, None),
        (
            {
                "state": "ok",
                "drift_detected": False,
                "performance": {"auc": 0.78, "labelled_rows": 500},
            },
            None,
        ),
        ({"state": "drift", "drift_detected": True}, "drift"),
        (
            {
                "state": "ok",
                "drift_detected": False,
                "performance": {"auc": 0.62, "labelled_rows": 500},
            },
            "performance",
        ),
        # too few labels to trust the AUC
        (
            {
                "state": "ok",
                "drift_detected": False,
                "performance": {"auc": 0.40, "labelled_rows": 10},
            },
            None,
        ),
    ],
)
def test_drift_reason(status, reason):
    assert module.drift_reason(status) == reason


def test_cooldown_blocks_a_second_trigger_while_drift_persists():
    from datetime import datetime, timedelta

    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    assert module.in_cooldown([now - timedelta(hours=2)], now, 6)
    assert not module.in_cooldown([now - timedelta(hours=7)], now, 6)
    assert not module.in_cooldown([], now, 6) and not module.in_cooldown([None], now, 6)
