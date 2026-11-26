"""Run under the isolated Airflow interpreter."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from airflow.exceptions import AirflowFailException
from airflow.models import DagBag
from pipelines.airflow.dags import ml_orchestrator_dag as module
from pipelines.airflow.dags import orchestration_common as common


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


# ---- found in the first live acceptance run ---------------------------------------------------


def test_request_id_is_unique_per_installation_but_stable_within_a_run():
    from datetime import UTC, datetime

    scheduled = "scheduled__2026-11-15T00:00:00+00:00"
    mine = datetime(2026, 11, 25, 17, 56, 10, 746708, tzinfo=UTC)
    theirs = datetime(2026, 11, 25, 17, 43, 49, 120000, tzinfo=UTC)
    # Two Airflow installations running the same scheduled interval must not share an id
    assert common.request_id(scheduled, mine) != common.request_id(scheduled, theirs)
    # ...but one run keeps its id across tasks and retries
    assert common.request_id(scheduled, mine) == common.request_id(scheduled, mine)
    # and different runs of one installation differ
    assert common.request_id("manual__a", mine) != common.request_id("manual__b", mine)
    assert len(common.request_id(scheduled, mine)) == 32
    assert common.request_id(scheduled, None)  # a run that has not started still gets an id


def test_workspace_uses_the_run_start_time(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setenv("PHASE5_WORK", "/tmp/phase5")  # noqa: S108
    first = SimpleNamespace(start_date=datetime(2026, 1, 1, tzinfo=UTC))
    second = SimpleNamespace(start_date=datetime(2026, 1, 2, tzinfo=UTC))
    a = common.workspace({"run_id": "scheduled__x", "dag_run": first})
    b = common.workspace({"run_id": "scheduled__x", "dag_run": second})
    assert a != b and a.parent == b.parent


def _helper_result(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=code, stdout=stdout, stderr=stderr)


def test_validation_failure_fails_the_task_without_retrying():
    failed = _helper_result(2, stderr="bad rows")
    with (
        patch.object(common.subprocess, "run", return_value=failed),
        pytest.raises(AirflowFailException, match="not retrying"),
    ):
        common.execute("python", "pipelines.phase5.airflow_data", "validate", "work", "corrupt")


def test_gate_rejection_fails_the_task_without_retrying():
    rejected = _helper_result(3, stderr="GateRejected: Quality gate rejected training run abc")
    with (
        patch.object(common.subprocess, "run", return_value=rejected),
        pytest.raises(AirflowFailException, match="quality gate rejected; not retrying"),
    ):
        common.execute("python", "pipelines.phase5.airflow_data", "gate", "work", "uci")


def test_other_failures_are_still_retryable():
    failed = _helper_result(1, stderr="network")
    with (
        patch.object(common.subprocess, "run", return_value=failed),
        pytest.raises(subprocess.CalledProcessError),
    ):
        common.execute("python", "pipelines.phase5.airflow_data", "fetch", "work", "uci")


def test_helper_output_reaches_the_task_log(caplog):
    done = _helper_result(0, stdout="pushed 30000 rows", stderr="a warning")
    with patch.object(common.subprocess, "run", return_value=done), caplog.at_level("INFO"):
        common.execute("python", "pipelines.phase5.airflow_data", "push", "work", "uci")
    assert "pushed 30000 rows" in caplog.text and "a warning" in caplog.text
