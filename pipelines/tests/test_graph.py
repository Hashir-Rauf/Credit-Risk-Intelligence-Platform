"""Run under the KFP SDK interpreter with pytest installed."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from kfp import compiler
from pipelines.kubeflow import client, pipeline_definition


def test_compiled_graph_matches_source(tmp_path):
    path = tmp_path / "pipeline.yaml"
    compiler.Compiler().compile(pipeline_definition.credit_risk, str(path))
    docs = list(yaml.safe_load_all(path.read_text()))
    committed = Path(pipeline_definition.PACKAGE).read_text()
    assert docs == list(yaml.safe_load_all(committed))
    tasks = docs[0]["root"]["dag"]["tasks"]
    assert tasks["train-tune"]["dependentTasks"] == ["preprocess"]
    assert set(tasks["evaluate"]["dependentTasks"]) == {"preprocess", "train-tune"}
    branch = next(t for t in tasks.values() if t.get("triggerPolicy"))
    assert "true" in branch["triggerPolicy"]["condition"].lower()
    nested = docs[0]["components"][branch["componentRef"]["name"]]["dag"]["tasks"]
    assert list(nested) == ["package-register"]
    assert all(not t.get("cachingOptions", {}).get("enableCache") for t in tasks.values())
    executors = docs[1]["platforms"]["kubernetes"]["deploymentSpec"]["executors"]
    assert all(
        v["secretAsEnv"][0]["secretName"] == "credit-risk-phase5" for v in executors.values()
    )


def test_submission_retry_reuses_remote_run():
    sdk = Mock()
    sdk.list_runs.return_value.runs = [SimpleNamespace(run_id="accepted")]
    assert client.submit(sdk, "r", {}, False, "rev") == "accepted"
    sdk.create_run_from_pipeline_package.assert_not_called()


def test_ambiguous_runs_fail_closed():
    sdk = Mock()
    sdk.list_runs.return_value.runs = [Mock(), Mock()]
    with pytest.raises(RuntimeError, match="Multiple KFP runs"):
        client.submit(sdk, "r", {}, False, "rev")
