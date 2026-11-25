"""Run under the isolated Airflow interpreter."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from airflow.models import DagBag
from pipelines.airflow.dags import ml_orchestrator_dag as module


def test_airflow_import_and_edges():
    folder = Path(module.__file__).parent
    bag = DagBag(str(folder), include_examples=False)
    assert not bag.import_errors
    dag = bag.dags["ml_orchestrator_dag"]
    order = [
        "fetch_data",
        "validate",
        "dvc_push",
        "trigger_kfp_run",
        "poll_run",
        "check_gate",
        "notify",
    ]
    assert set(dag.task_ids) == set(order)
    for first, second in zip(order, order[1:], strict=False):
        assert dag.get_task(first).downstream_task_ids == {second}
    assert dag.max_active_runs == 1
    assert dag.get_task("fetch_data").retries == 2


def test_retry_demo_only_interrupts_first_attempt():
    context = {"params": {"retry_demo": True}, "ti": SimpleNamespace(try_number=1)}
    with (
        patch.object(module, "get_current_context", return_value=context),
        patch.object(module, "data_task") as fetch,
    ):
        call = module.dag.get_task("fetch_data").python_callable
        with pytest.raises(RuntimeError, match="retry demonstration"):
            call()
        context["ti"].try_number = 2
        call()
        fetch.assert_called_once_with("fetch")
