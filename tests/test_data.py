"""Cycle 1 tests: synthetic data, ingestion, validation, DVC pipeline wiring."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import types
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import xlwt
import yaml

from src.common.cli import ExitCode, stage_main
from src.common.config import get_param
from src.common.errors import ConfigError, DataValidationError, IngestionError
from src.common.io import sha256_file
from src.data import ingestion, validation
from src.data.paths import (
    ingestion_report_path,
    raw_csv_path,
    validation_report_path,
)
from src.data.schema import canonical_columns
from src.data.synthetic import make_synthetic_credit_data

# --------------------------------------------------------------------------- helpers


def uci_style(df: pd.DataFrame) -> pd.DataFrame:
    """Rename canonical columns to the spellings used by the real UCI sheet."""
    return df.rename(columns={"PAY_1": "PAY_0", "default_next_month": "default payment next month"})


def make_uci_xls(df: pd.DataFrame) -> bytes:
    """Build an .xls shaped like the UCI file: a 'X1..X23,Y' label row above the header row."""
    uci = uci_style(df)
    book = xlwt.Workbook()
    sheet = book.add_sheet("Data")
    labels = [""] + [f"X{i}" for i in range(1, len(uci.columns) - 1)] + ["Y"]
    for j, label in enumerate(labels):
        sheet.write(0, j, label)
    for j, name in enumerate(uci.columns):
        sheet.write(1, j, name)
    for i, row in enumerate(uci.itertuples(index=False), start=2):
        for j, value in enumerate(row):
            sheet.write(i, j, int(value))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def zip_bytes(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, n: int = -1) -> bytes:
        return self._content if n < 0 else self._content[:n]


def errors_for(report: dict[str, Any], column: str) -> list[dict[str, Any]]:
    return [e for e in report["errors"] if e["column"] == column]


# --------------------------------------------------------------------------- synthetic


class TestSynthetic:
    def test_columns_and_dtypes_match_canonical_schema(self, params):
        df = make_synthetic_credit_data(500, seed=3)
        assert list(df.columns) == canonical_columns(params)
        assert (df.dtypes == "int64").all()

    def test_deterministic_for_same_seed_and_different_otherwise(self):
        a = make_synthetic_credit_data(400, seed=5)
        assert a.equals(make_synthetic_credit_data(400, seed=5))
        assert not a.equals(make_synthetic_credit_data(400, seed=6))

    def test_ids_unique_and_sequential(self):
        df = make_synthetic_credit_data(300, seed=1)
        assert df["ID"].tolist() == list(range(1, 301))

    def test_default_rate_near_uci(self):
        rate = make_synthetic_credit_data(5000, seed=2)["default_next_month"].mean()
        assert 0.18 <= rate <= 0.27

    def test_pay_1_carries_signal(self):
        df = make_synthetic_credit_data(5000, seed=2)
        late = df.loc[df["PAY_1"] >= 2, "default_next_month"].mean()
        on_time = df.loc[df["PAY_1"] <= 0, "default_next_month"].mean()
        assert late > on_time + 0.2

    def test_contains_the_real_datas_undocumented_codes(self):
        df = make_synthetic_credit_data(5000, seed=2)
        assert {5, 6} <= set(df["EDUCATION"])
        assert 0 in set(df["MARRIAGE"])
        assert {-2, 0} <= set(df["PAY_1"])

    def test_passes_validation(self, iso_params):
        df = make_synthetic_credit_data(1500, seed=9)
        report = validation.validate_dataframe(df, iso_params)
        assert report["status"] == "passed", report["errors"]


# --------------------------------------------------------------------------- paths


class TestPaths:
    def test_normal_paths_follow_params(self, params, root):
        assert raw_csv_path(params) == root / "data/raw/default_credit_card.csv"
        assert ingestion_report_path(params) == root / "reports/ingestion_report.json"
        assert validation_report_path(params) == root / "reports/validation_report.json"

    def test_smoke_paths_are_isolated(self, params, root):
        assert raw_csv_path(params, smoke=True) == root / "data/smoke/raw/default_credit_card.csv"
        assert (
            validation_report_path(params, smoke=True)
            == root / "data/smoke/reports/validation_report.json"
        )
        assert "smoke" in str(ingestion_report_path(params, smoke=True))


# --------------------------------------------------------------------------- normalisation


class TestNormalizeColumns:
    def test_uci_headers_become_canonical(self, params):
        df = uci_style(make_synthetic_credit_data(50, seed=1))
        out = ingestion.normalize_columns(df, params)
        assert list(out.columns) == canonical_columns(params)

    def test_kaggle_and_messy_headers(self, params):
        base = make_synthetic_credit_data(20, seed=1)
        messy = base.rename(
            columns={
                "PAY_1": "pay_0",
                "default_next_month": "default.payment.next.month",
                "LIMIT_BAL": " Limit_Bal ",
                "BILL_AMT1": "bill_amt1",
            }
        )
        out = ingestion.normalize_columns(messy, params)
        assert list(out.columns) == canonical_columns(params)

    def test_pay_0_and_pay_1_together_is_an_error(self, params):
        df = make_synthetic_credit_data(20, seed=1)
        df["PAY_0"] = df["PAY_1"]
        with pytest.raises(IngestionError, match="duplicate columns"):
            ingestion.normalize_columns(df, params)

    def test_unknown_columns_are_kept_for_validation_to_report(self, params):
        df = make_synthetic_credit_data(20, seed=1)
        df["surprise"] = 1
        assert "surprise" in ingestion.normalize_columns(df, params).columns

    def test_integral_floats_become_int_but_fractions_do_not(self, params):
        df = make_synthetic_credit_data(20, seed=1)
        df["AGE"] = df["AGE"].astype(float)
        df["LIMIT_BAL"] = df["LIMIT_BAL"].astype(float) + 0.5
        df.loc[0, "SEX"] = None
        df["SEX"] = df["SEX"].astype(float)
        out = ingestion.normalize_columns(df, params)
        assert out["AGE"].dtype == "int64"
        assert out["LIMIT_BAL"].dtype == "float64"  # has fractions
        assert out["SEX"].dtype == "float64"  # has a null, must stay visible to validation


# --------------------------------------------------------------------------- readers


class TestReaders:
    def test_csv(self):
        out = ingestion._read_table(b"a,b\n1,2\n", "x.csv")
        assert out.to_dict("records") == [{"a": 1, "b": 2}]

    def test_empty_csv_is_ingestion_error(self):
        with pytest.raises(IngestionError, match="cannot parse CSV"):
            ingestion._read_table(b"", "x.csv")

    def test_unsupported_suffix(self):
        with pytest.raises(IngestionError, match="unsupported"):
            ingestion._read_table(b"{}", "x.json")

    def test_uci_style_xls_with_label_row(self, params):
        df = make_synthetic_credit_data(40, seed=4)
        raw = ingestion._read_table(make_uci_xls(df), "default of credit card clients.xls")
        out = ingestion.normalize_columns(raw, params)
        assert list(out.columns) == canonical_columns(params)
        assert out.equals(df)

    def test_xls_without_header_row(self):
        book = xlwt.Workbook()
        book.add_sheet("Data").write(0, 0, "nothing useful")
        buffer = io.BytesIO()
        book.save(buffer)
        with pytest.raises(IngestionError, match="LIMIT_BAL"):
            ingestion._read_table(buffer.getvalue(), "x.xls")

    def test_corrupt_xls(self):
        with pytest.raises(IngestionError, match="cannot read Excel"):
            ingestion._read_table(b"not an excel file", "x.xls")

    def test_zip_picks_the_table(self):
        content, name = ingestion._pick_table_in_zip(zip_bytes("data/d.csv", b"a\n1\n"))
        assert (content, name) == (b"a\n1\n", "data/d.csv")

    def test_zip_without_table(self):
        with pytest.raises(IngestionError, match="no"):
            ingestion._pick_table_in_zip(zip_bytes("readme.txt", b"hi"))

    def test_not_a_zip(self):
        with pytest.raises(IngestionError, match="valid zip"):
            ingestion._pick_table_in_zip(b"plain bytes")


# --------------------------------------------------------------------------- download / s3


class TestDownload:
    def test_success_sends_user_agent(self, monkeypatch):
        seen: dict[str, Any] = {}

        def fake_urlopen(request, timeout):
            seen["ua"] = request.get_header("User-agent")
            seen["timeout"] = timeout
            return FakeResponse(b"payload")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert ingestion._download("https://example.org/f.zip") == b"payload"
        assert seen["ua"] == "credit-risk-platform-ingestion"
        assert seen["timeout"] == ingestion.DOWNLOAD_TIMEOUT_S

    def test_network_failure_is_ingestion_error(self, monkeypatch):
        def boom(request, timeout):
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(IngestionError, match="download failed"):
            ingestion._download("https://example.org/f.zip")

    def test_oversized_download_rejected(self, monkeypatch):
        monkeypatch.setattr(ingestion, "MAX_DOWNLOAD_BYTES", 10)
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"x" * 50)
        )
        with pytest.raises(IngestionError, match="exceeds"):
            ingestion._download("https://example.org/f.zip")

    def test_plain_http_refused(self):
        with pytest.raises(ConfigError, match="https"):
            ingestion._download("http://example.org/f.zip")


class FakeS3Client:
    def __init__(self, payload: bytes, fail: bool = False) -> None:
        self.payload, self.fail, self.calls = payload, fail, []

    def download_fileobj(self, bucket: str, key: str, buffer: io.BytesIO) -> None:
        self.calls.append((bucket, key))
        if self.fail:
            raise RuntimeError("AccessDenied")
        buffer.write(self.payload)


def install_fake_boto3(monkeypatch, client: FakeS3Client) -> None:
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: client))


class TestS3:
    def test_downloads_bucket_and_key(self, monkeypatch, iso_params):
        client = FakeS3Client(b"a,b\n1,2\n")
        install_fake_boto3(monkeypatch, client)
        monkeypatch.setenv("RAW_DATA_S3_URI", "s3://my-bucket/raw/data.csv")
        content, key = ingestion._fetch_s3(iso_params)
        assert content == b"a,b\n1,2\n"
        assert client.calls == [("my-bucket", "raw/data.csv")]
        assert key == "raw/data.csv"

    @pytest.mark.parametrize("value", ["", "http://x/y", "s3://bucket", "s3:///key"])
    def test_bad_uri_is_config_error(self, monkeypatch, iso_params, value):
        monkeypatch.setenv("RAW_DATA_S3_URI", value)
        with pytest.raises(ConfigError, match="RAW_DATA_S3_URI"):
            ingestion._fetch_s3(iso_params)

    def test_unset_env_is_config_error(self, monkeypatch, iso_params):
        monkeypatch.delenv("RAW_DATA_S3_URI", raising=False)
        with pytest.raises(ConfigError):
            ingestion._fetch_s3(iso_params)

    def test_s3_failure_is_ingestion_error(self, monkeypatch, iso_params):
        install_fake_boto3(monkeypatch, FakeS3Client(b"", fail=True))
        monkeypatch.setenv("RAW_DATA_S3_URI", "s3://b/k.csv")
        with pytest.raises(IngestionError, match="S3 download failed"):
            ingestion._fetch_s3(iso_params)

    def test_missing_boto3(self, monkeypatch, iso_params):
        monkeypatch.setitem(sys.modules, "boto3", None)
        monkeypatch.setenv("RAW_DATA_S3_URI", "s3://b/k.csv")
        with pytest.raises(IngestionError, match="boto3"):
            ingestion._fetch_s3(iso_params)

    def test_full_run_from_s3_zip_of_xls(self, monkeypatch, iso_params, valid_df):
        payload = zip_bytes("clients.xls", make_uci_xls(valid_df.head(600)))
        install_fake_boto3(monkeypatch, FakeS3Client(payload))
        monkeypatch.setenv("RAW_DATA_S3_URI", "s3://b/uci.zip")
        iso_params["data"]["source"] = "s3"
        result = ingestion.run(iso_params, smoke=False)
        assert result["source"] == "s3"
        assert result["rows"] == 600


# --------------------------------------------------------------------------- ingestion stage


class TestIngestionRun:
    def test_synthetic_writes_csv_and_report(self, iso_params):
        out = ingestion.run(iso_params)
        csv = Path(out["raw_csv"])
        df = pd.read_csv(csv)
        assert list(df.columns) == canonical_columns(iso_params)
        assert len(df) == 1500
        report = json.loads(ingestion_report_path(iso_params).read_text())
        assert report["synthetic"] is True
        assert report["source"] == "synthetic"
        assert report["rows"] == 1500
        assert report["sha256"] == sha256_file(csv)

    def test_output_is_byte_identical_across_runs_and_uses_lf(self, iso_params):
        csv = Path(ingestion.run(iso_params)["raw_csv"])
        first = csv.read_bytes()
        ingestion.run(iso_params)
        assert csv.read_bytes() == first
        assert b"\r\n" not in first

    def test_seed_changes_data(self, iso_params):
        csv = Path(ingestion.run(iso_params)["raw_csv"])
        first = sha256_file(csv)
        iso_params["seed"] = 43
        ingestion.run(iso_params)
        assert sha256_file(csv) != first

    def test_local_csv_and_xls(self, iso_params, valid_df):
        local = Path(iso_params["data"]["local_path"])
        iso_params["data"]["source"] = "local"

        uci_style(valid_df).to_csv(local, index=False)
        assert ingestion.run(iso_params)["rows"] == len(valid_df)

        xls = local.with_suffix(".xls")
        xls.write_bytes(make_uci_xls(valid_df.head(700)))
        iso_params["data"]["local_path"] = str(xls)
        assert ingestion.run(iso_params)["rows"] == 700

    def test_local_missing_file(self, iso_params):
        iso_params["data"]["source"] = "local"
        with pytest.raises(IngestionError, match="not found"):
            ingestion.run(iso_params)

    def test_uci_source_end_to_end_with_mocked_download(self, monkeypatch, iso_params, valid_df):
        payload = zip_bytes("default of credit card clients.xls", make_uci_xls(valid_df))
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda request, timeout: FakeResponse(payload)
        )
        iso_params["data"]["source"] = "uci"
        out = ingestion.run(iso_params)
        report = validation.run(iso_params)
        assert out["source"] == "uci" and report["status"] == "passed"
        assert json.loads(ingestion_report_path(iso_params).read_text())["synthetic"] is False
        assert pd.read_csv(out["raw_csv"]).equals(valid_df)

    def test_smoke_forces_synthetic_and_never_touches_raw_dir(self, iso_params, tmp_path):
        iso_params["data"]["source"] = "s3"  # would fail (no env var) if smoke honoured it
        out = ingestion.run(iso_params, smoke=True)
        assert out["source"] == "synthetic"
        assert out["rows"] == 800
        assert Path(out["raw_csv"]).is_relative_to(tmp_path / "smoke")
        assert not (tmp_path / "raw").exists()
        assert not ingestion_report_path(iso_params).exists()

    def test_extra_columns_survive_and_missing_ones_stay_missing(self, iso_params, valid_df):
        local = Path(iso_params["data"]["local_path"])
        iso_params["data"]["source"] = "local"
        frame = valid_df.assign(surprise=1).drop(columns=["AGE"])
        frame.to_csv(local, index=False)
        ingestion.run(iso_params)
        written = pd.read_csv(raw_csv_path(iso_params))
        assert "surprise" in written.columns and "AGE" not in written.columns
        with pytest.raises(DataValidationError) as exc:
            validation.run(iso_params)
        checks = {(e["column"], e["check"]) for e in exc.value.report["errors"]}
        assert ("surprise", "column_in_schema") in checks
        assert ("AGE", "column_in_dataframe") in checks

    def test_cli_smoke_exit_codes(self, iso_params, write_params):
        path = write_params(iso_params)
        assert (
            stage_main(ingestion.run, description="t", argv=["--params", str(path), "--smoke"]) == 0
        )
        iso_params["data"]["source"] = "local"
        path = write_params(iso_params)
        rc = stage_main(ingestion.run, description="t", argv=["--params", str(path)])
        assert rc == ExitCode.ERROR  # missing local file is an error, not validation failure


# --------------------------------------------------------------------------- validation


def set_cell(df: pd.DataFrame, column: str, value: Any, row: int = 0) -> pd.DataFrame:
    out = df.copy()
    if isinstance(value, str):
        out[column] = out[column].astype(object)
    out.loc[row, column] = value
    return out


class TestValidation:
    @pytest.mark.parametrize(
        ("mutate", "column", "check"),
        [
            (lambda d: set_cell(d, "LIMIT_BAL", float("nan")), "LIMIT_BAL", "not_nullable"),
            (lambda d: set_cell(d, "LIMIT_BAL", 0), "LIMIT_BAL", "in_range"),
            (lambda d: set_cell(d, "ID", d.loc[1, "ID"], row=0), "ID", "uniqueness"),
            (lambda d: set_cell(d, "SEX", 3), "SEX", "isin"),
            (lambda d: set_cell(d, "EDUCATION", 7), "EDUCATION", "isin"),
            (lambda d: set_cell(d, "MARRIAGE", 4), "MARRIAGE", "isin"),
            (lambda d: set_cell(d, "AGE", 17), "AGE", "in_range"),
            (lambda d: set_cell(d, "AGE", 150), "AGE", "in_range"),
            (lambda d: set_cell(d, "PAY_3", 10), "PAY_3", "in_range"),
            (lambda d: set_cell(d, "PAY_1", -3), "PAY_1", "in_range"),
            (lambda d: set_cell(d, "PAY_AMT2", -5), "PAY_AMT2", "in_range"),
            (lambda d: set_cell(d, "BILL_AMT6", 10**9), "BILL_AMT6", "in_range"),
            (lambda d: set_cell(d, "default_next_month", 2), "default_next_month", "isin"),
            (lambda d: set_cell(d, "BILL_AMT1", "abc"), "BILL_AMT1", "dtype"),
            (
                lambda d: d.assign(AGE=d["AGE"].astype(float)).pipe(set_cell, "AGE", 30.5),
                "AGE",
                "dtype",
            ),
            (lambda d: d.assign(extra=1), "extra", "column_in_schema"),
            (lambda d: d.drop(columns=["PAY_AMT4"]), "PAY_AMT4", "column_in_dataframe"),
            (lambda d: d.assign(default_next_month=0), "default_next_month", "target_rate_in"),
            (lambda d: d.assign(default_next_month=1), "default_next_month", "target_rate_in"),
            (lambda d: d.head(100), "<dataframe>", "min_rows"),
        ],
    )
    def test_each_violation_fails_with_the_right_check(
        self, iso_params, valid_df, mutate, column, check
    ):
        report = validation.validate_dataframe(mutate(valid_df), iso_params)
        assert report["status"] == "failed"
        hits = [e for e in errors_for(report, column) if check in e["check"]]
        assert hits, f"expected {check!r} on {column!r}, got {report['errors']}"

    def test_valid_data_passes_with_no_errors(self, iso_params, valid_df):
        report = validation.validate_dataframe(valid_df, iso_params)
        assert report["status"] == "passed"
        assert report["errors"] == []
        assert report["rows"] == 1500 and report["columns"] == 25
        assert 0.15 < report["target_rate"] < 0.30

    def test_undocumented_codes_warn_but_do_not_fail(self, iso_params, valid_df):
        df = set_cell(set_cell(valid_df, "EDUCATION", 5, row=0), "MARRIAGE", 0, row=1)
        report = validation.validate_dataframe(df, iso_params)
        assert report["status"] == "passed"
        by_col = {w["column"]: w for w in report["warnings"] if w["type"] == "undocumented_codes"}
        assert by_col["EDUCATION"]["counts"]["5"] >= 1
        assert by_col["MARRIAGE"]["counts"]["0"] >= 1
        assert "PAY_1" in by_col  # -2 / 0 codes are grouped under PAY

    def test_duplicate_rows_excluding_id_warn(self, iso_params, valid_df):
        df = valid_df.copy()
        df.loc[1] = df.loc[0].tolist()
        df.loc[1, "ID"] = 99999
        report = validation.validate_dataframe(df, iso_params)
        assert report["status"] == "passed"
        assert {"type": "duplicate_rows_excluding_id", "count": 1} in report["warnings"]

    def test_all_problems_reported_together(self, iso_params, valid_df):
        df = set_cell(set_cell(valid_df, "SEX", 3), "AGE", 17)
        report = validation.validate_dataframe(df, iso_params)
        assert {e["column"] for e in report["errors"]} >= {"SEX", "AGE"}

    def test_report_is_json_serialisable_and_examples_are_capped(self, iso_params, valid_df):
        df = valid_df.copy()
        df["SEX"] = 9
        report = validation.validate_dataframe(df, iso_params)
        json.dumps(report)
        sex = errors_for(report, "SEX")[0]
        assert sex["count"] == 1500
        assert len(sex["examples"]) == validation.MAX_EXAMPLES
        assert set(report) == {"status", "rows", "columns", "target_rate", "errors", "warnings"}

    def test_input_frame_not_mutated(self, iso_params, valid_df):
        before = valid_df.copy()
        validation.validate_dataframe(valid_df, iso_params)
        assert valid_df.equals(before)

    def test_threshold_changes_in_params_take_effect(self, iso_params, valid_df):
        iso_params["validation"]["domains"]["SEX"] = [1]
        report = validation.validate_dataframe(valid_df, iso_params)
        assert errors_for(report, "SEX")


class TestValidationRun:
    def prepare(self, params: dict[str, Any], df: pd.DataFrame) -> None:
        path = raw_csv_path(params)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)

    def test_pass_writes_deterministic_report(self, iso_params, valid_df):
        self.prepare(iso_params, valid_df)
        out = validation.run(iso_params)
        assert out["status"] == "passed"
        path = validation_report_path(iso_params)
        first = path.read_bytes()
        validation.run(iso_params)
        assert path.read_bytes() == first
        assert json.loads(first)["status"] == "passed"

    def test_failure_writes_report_then_raises(self, iso_params, valid_df):
        self.prepare(iso_params, set_cell(valid_df, "SEX", 3))
        with pytest.raises(DataValidationError, match="SEX") as exc:
            validation.run(iso_params)
        written = json.loads(validation_report_path(iso_params).read_text())
        assert written["status"] == "failed"
        assert written == exc.value.report

    def test_cli_exit_code_2_on_bad_data_and_0_on_good(self, iso_params, valid_df, write_params):
        argv = ["--params", str(write_params(iso_params))]
        self.prepare(iso_params, valid_df)
        assert stage_main(validation.run, description="t", argv=argv) == 0
        self.prepare(iso_params, set_cell(valid_df, "EDUCATION", 9))
        assert stage_main(validation.run, description="t", argv=argv) == ExitCode.VALIDATION_FAILED

    def test_missing_raw_file_is_error_not_validation_failure(self, iso_params, write_params):
        argv = ["--params", str(write_params(iso_params))]
        assert stage_main(validation.run, description="t", argv=argv) == ExitCode.ERROR

    def test_unreadable_csv_fails_validation_with_report(self, iso_params):
        path = raw_csv_path(iso_params)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        with pytest.raises(DataValidationError, match="not a readable CSV"):
            validation.run(iso_params)
        report = json.loads(validation_report_path(iso_params).read_text())
        assert report["errors"][0]["check"] == "csv_readable"

    def test_smoke_reads_and_writes_only_the_smoke_dir(self, iso_params, tmp_path):
        ingestion.run(iso_params, smoke=True)
        out = validation.run(iso_params, smoke=True)
        assert out["status"] == "passed"
        assert Path(out["validation_report"]).is_relative_to(tmp_path / "smoke")
        assert not validation_report_path(iso_params).exists()


# --------------------------------------------------------------------------- config for new keys


class TestNewConfigValidation:
    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda p: p["data"].update(uci_url="http://insecure.example/x.zip"), "https"),
            (lambda p: p["data"].update(synthetic_rows=0), "positive integer"),
            (lambda p: p["smoke"].update(n_rows=True), "positive integer"),
            (lambda p: p["validation"].update(min_rows=-5), "positive integer"),
            (lambda p: p["validation"].update(target_rate_bounds=[0.6, 0.1]), "target_rate_bounds"),
            (lambda p: p["validation"].update(target_rate_bounds=[0.0, 0.5]), "target_rate_bounds"),
            (lambda p: p["validation"]["domains"].update(SEX=[]), "domains.SEX"),
            (lambda p: p["validation"]["ranges"].update(AGE=[50, 18]), "ranges.AGE"),
            (lambda p: p["validation"]["ranges"].update(PAY="wide"), "ranges.PAY"),
            (lambda p: p["validation"].update(undocumented_codes=["x"]), "undocumented_codes"),
            (lambda p: p["data"].update(source="ftp"), "data.source"),
        ],
    )
    def test_invalid_values_rejected(self, params, mutate, message):
        from src.common.config import validate_params

        mutate(params)
        with pytest.raises(ConfigError, match=message):
            validate_params(params)

    def test_synthetic_is_an_allowed_source(self, params):
        from src.common.config import validate_params

        params["data"]["source"] = "synthetic"
        validate_params(params)


# --------------------------------------------------------------------------- DVC wiring


@pytest.fixture(scope="module")
def dvc_yaml(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / "dvc.yaml").read_text(encoding="utf-8"))


def out_path(entry: Any) -> str:
    return entry if isinstance(entry, str) else next(iter(entry))


class TestDvcWiring:
    def test_stage_order_and_commands(self, dvc_yaml):
        stages = dvc_yaml["stages"]
        assert list(stages)[:2] == ["ingest", "validate"]
        assert stages["ingest"]["cmd"] == "python -m src.data.ingestion"
        assert stages["validate"]["cmd"] == "python -m src.data.validation"

    def test_validate_depends_on_ingest_output(self, dvc_yaml):
        ingest_outs = {out_path(o) for o in dvc_yaml["stages"]["ingest"]["outs"]}
        assert ingest_outs & set(dvc_yaml["stages"]["validate"]["deps"])

    def test_outputs_match_params_paths(self, dvc_yaml, params):
        raw = f"{params['data']['raw_dir']}/{params['data']['raw_filename']}"
        ingest = {out_path(o) for o in dvc_yaml["stages"]["ingest"]["outs"]}
        validate = {out_path(o) for o in dvc_yaml["stages"]["validate"]["outs"]}
        assert raw in ingest
        assert params["data"]["ingestion_report"] in ingest
        assert validate == {params["validation"]["report_path"]}

    def test_report_outputs_are_git_tracked_not_cached(self, dvc_yaml):
        for stage in dvc_yaml["stages"].values():
            for entry in stage["outs"]:
                if out_path(entry).startswith("reports/"):
                    assert entry[out_path(entry)] == {"cache": False}

    def test_declared_params_exist_in_params_yaml(self, dvc_yaml, params):
        for stage in dvc_yaml["stages"].values():
            for key in stage["params"]:
                assert get_param(params, key, None) is not None, key

    def test_every_dependency_path_exists(self, dvc_yaml, root):
        for name, stage in dvc_yaml["stages"].items():
            for dep in stage["deps"]:
                produced = any(
                    out_path(o) == dep for s in dvc_yaml["stages"].values() for o in s["outs"]
                )
                assert produced or (root / dep).exists(), f"{name}: missing dep {dep}"

    def test_dvcignore_excludes_bytecode(self, root):
        text = (root / ".dvcignore").read_text(encoding="utf-8")
        assert "__pycache__/" in text and "*.pyc" in text

    def test_committed_dvc_config_has_no_credentials(self, root):
        text = (root / ".dvc" / "config").read_text(encoding="utf-8").lower()
        for needle in ("access_key", "secret", "password", "token"):
            assert f"{needle} =" not in text and f"{needle}=" not in text

    def test_gitignore_covers_smoke_and_dvc_local(self, root):
        text = (root / ".gitignore").read_text(encoding="utf-8")
        assert "data/smoke/" in text
        assert ".dvc/config.local" in text


_TOP_LEVEL_SKIP = {".git", "data", "reports", ".venv", "tests"}
_ALWAYS_SKIP = {"__pycache__", ".pytest_cache", ".ruff_cache", "cache", "tmp", "config.local"}


def copy_repo(root: Path, dest: Path) -> None:
    """Copy the project (code, params, dvc.yaml, .dvc/config) without data, caches or tests."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in _ALWAYS_SKIP or n.endswith((".tar.gz", ".pyc"))}
        if Path(directory) == root:
            skip |= {n for n in names if n in _TOP_LEVEL_SKIP}
        return skip

    shutil.copytree(root, dest, ignore=ignore)


def tool_env() -> dict[str, str]:
    """Environment where `python` is this interpreter (dvc runs stage cmds through a shell)."""
    return {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        "DVC_NO_ANALYTICS": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }


def sh(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def edit_params(repo: Path, mutate) -> dict[str, Any]:
    path = repo / "params.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return data


@pytest.fixture()
def dvc_repo(root: Path, tmp_path: Path) -> Path:
    pytest.importorskip("dvc")
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    copy_repo(root, repo)
    edit_params(repo, lambda p: p["data"].update(source="synthetic", synthetic_rows=1500))
    assert sh(["git", "init", "-q", "-b", "main"], repo, tool_env()).returncode == 0
    return repo


DVC = [sys.executable, "-m", "dvc"]


@pytest.mark.integration
class TestDvcReproducibility:
    """Real `dvc repro` in a throwaway git repo: the spec's 'retrieve an earlier code version
    and its matching dataset' demonstration, automated. Uses synthetic data (no network)."""

    def test_repro_is_idempotent_and_old_data_is_restorable(self, dvc_repo):
        repo, env = dvc_repo, tool_env()
        first = sh([*DVC, "repro"], repo, env)
        assert first.returncode == 0, first.stdout + first.stderr
        raw = repo / "data" / "raw" / "default_credit_card.csv"
        assert raw.is_file() and (repo / "dvc.lock").is_file()
        v1_hash = sha256_file(raw)

        # Nothing changed: a second run must skip both stages.
        second = sh([*DVC, "repro"], repo, env)
        assert second.stdout.count("didn't change, skipping") == 2, second.stdout + second.stderr

        sh(["git", "add", "-A"], repo, env)
        assert sh(["git", "commit", "-qm", "v1"], repo, env).returncode == 0
        v1 = sh(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()

        # New params version -> new data version.
        edit_params(repo, lambda p: p.update(seed=7))
        assert sh([*DVC, "repro"], repo, env).returncode == 0
        v2_hash = sha256_file(raw)
        assert v2_hash != v1_hash
        sh(["git", "add", "-A"], repo, env)
        assert sh(["git", "commit", "-qm", "v2"], repo, env).returncode == 0

        # Back to v1: git restores code + dvc.lock, dvc checkout restores the matching data.
        assert sh(["git", "checkout", "-q", v1], repo, env).returncode == 0
        restored = sh([*DVC, "checkout"], repo, env)
        assert restored.returncode == 0, restored.stdout + restored.stderr
        assert sha256_file(raw) == v1_hash

    def test_validation_failure_stops_dvc_repro(self, dvc_repo):
        edit_params(dvc_repo, lambda p: p["validation"].update(min_rows=10**6))
        result = sh([*DVC, "repro"], dvc_repo, tool_env())
        assert result.returncode != 0
        assert "exited with 2" in result.stdout + result.stderr
        assert not (dvc_repo / "dvc.lock").exists() or "validate" not in (
            (dvc_repo / "dvc.lock").read_text()
        )
