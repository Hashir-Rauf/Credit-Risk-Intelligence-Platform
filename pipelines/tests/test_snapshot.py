"""Run under the ML interpreter."""

import json
from zipfile import ZipFile

import pytest
from pipelines.phase5 import airflow_data, bundles, snapshot
from src.common.io import sha256_file
from src.data.validation import DataValidationError


def test_validated_bytes_round_trip(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    monkeypatch.setenv("PHASE5_DVC_URL", str(remote))
    airflow_data.fetch(tmp_path, "synthetic")
    airflow_data.validate(tmp_path)
    csv = tmp_path / "raw.csv"
    report = tmp_path / "validation.json"
    descriptor = snapshot.publish(csv, report, "synthetic")
    destination = tmp_path / "download/fetched.csv"
    snapshot.retrieve(descriptor, destination)
    assert sha256_file(destination) == sha256_file(csv)
    csv.write_bytes(csv.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="validated input"):
        snapshot.publish(csv, report, "synthetic")


def test_corrupt_fixture_stops_validation(tmp_path):
    airflow_data.fetch(tmp_path, "corrupt")
    with pytest.raises(DataValidationError):
        airflow_data.validate(tmp_path)
    assert json.loads((tmp_path / "validation.json").read_text())["passed"] is False


def test_invalid_bundle_path_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    with ZipFile(path, "w") as out:
        out.writestr("reports/../../outside", "x")
    with pytest.raises(ValueError):
        bundles.unpack(path)
