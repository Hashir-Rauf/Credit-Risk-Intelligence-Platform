"""The data helper's command line, run as Airflow runs it (a real subprocess). ML environment."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(action, work, mode):
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pipelines.phase5.airflow_data", action, str(work), mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


def test_corrupt_data_exits_with_the_validation_code_and_names_the_problems(tmp_path):
    assert run("fetch", tmp_path, "corrupt").returncode == 0
    result = run("validate", tmp_path, "corrupt")
    assert result.returncode == 2  # the shared "validation failed" code; the DAG does not retry it
    assert "DataValidationError" in result.stderr
    report = json.loads((tmp_path / "validation.json").read_text())
    assert report["passed"] is False and report["failures"]


def test_good_data_exits_zero(tmp_path):
    assert run("fetch", tmp_path, "synthetic").returncode == 0
    assert run("validate", tmp_path, "synthetic").returncode == 0
    assert json.loads((tmp_path / "validation.json").read_text())["passed"] is True
