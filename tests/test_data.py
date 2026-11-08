"""Data tests: ingestion helpers and validation (including a deliberately corrupt fixture)."""

from pathlib import Path

import pandas as pd
import pytest
from src.data.ingestion import ChecksumError, apply_row_filters, sha256_bytes, zip_to_frame
from src.data.validation import DataValidationError, validate

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def good() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "credit_sample.csv")


def test_valid_sample_passes(good):
    report = validate(good)
    assert report["passed"] is True
    assert report["n_rows"] == 300
    assert 0 < report["default_rate"] < 1


def test_corrupt_fixture_fails_with_clear_message():
    bad = pd.read_csv(FIXTURES / "credit_corrupt.csv")
    with pytest.raises(DataValidationError) as exc:
        validate(bad)
    columns = {f["column"] for f in exc.value.report["failures"]}
    assert {"AGE", "SEX", "default", "LIMIT_BAL", "PAY_AMT1", "customer_id"} <= columns
    assert "Data validation failed" in str(exc.value)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("AGE", 17),
        ("AGE", 150),
        ("SEX", 0),
        ("EDUCATION", 9),
        ("MARRIAGE", 7),
        ("PAY_1", 12),
        ("PAY_AMT3", -5),
        ("default", 2),
        ("LIMIT_BAL", 0),
    ],
)
def test_out_of_range_value_fails(good, column, value):
    good.loc[0, column] = value
    with pytest.raises(DataValidationError):
        validate(good)


def test_null_fails(good):
    good["AGE"] = good["AGE"].astype(float)
    good.loc[0, "AGE"] = None
    with pytest.raises(DataValidationError):
        validate(good)


def test_duplicate_ids_fail(good):
    good.loc[1, "customer_id"] = good.loc[0, "customer_id"]
    with pytest.raises(DataValidationError):
        validate(good)


def test_missing_column_fails(good):
    with pytest.raises(DataValidationError):
        validate(good.drop(columns=["AGE"]))


def test_unexpected_column_fails(good):
    good["surprise"] = 1
    with pytest.raises(DataValidationError):
        validate(good)


def test_wrong_dtype_fails(good):
    good["AGE"] = good["AGE"].astype(str)
    with pytest.raises(DataValidationError):
        validate(good)


def test_empty_frame_fails(good):
    with pytest.raises(DataValidationError):
        validate(good.iloc[0:0])


def test_sha256_bytes_is_stable():
    assert sha256_bytes(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_checksum_error_is_a_runtime_error():
    assert issubclass(ChecksumError, RuntimeError)


def test_zip_to_frame_renames_columns(tmp_path):
    import io
    import zipfile

    raw = pd.DataFrame({"ID": [1], "PAY_0": [2], "default payment next month": [1]})
    buf_xls = tmp_path / "x.xlsx"
    with pd.ExcelWriter(buf_xls) as writer:
        pd.DataFrame([["title"]]).to_excel(writer, index=False, header=False, startrow=0)
        raw.to_excel(writer, index=False, startrow=1)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.write(buf_xls, "data.xlsx")
    df = zip_to_frame(zbuf.getvalue(), "data.xlsx")
    assert list(df.columns) == ["customer_id", "PAY_1", "default"]


def test_row_filters_off_by_default(good):
    assert len(apply_row_filters(good, {})) == len(good)


def test_drop_duplicate_rows_ignores_customer_id(good):
    dup = good.iloc[[0]].copy()
    dup["customer_id"] = 999_999
    df = pd.concat([good, dup], ignore_index=True)
    out = apply_row_filters(df, {"drop_duplicate_rows": True})
    assert len(out) == len(good)


def test_drop_unknown_education_keeps_only_documented_codes(good):
    good.loc[0, "EDUCATION"] = 5
    out = apply_row_filters(good, {"drop_unknown_education": True})
    assert out["EDUCATION"].isin([1, 2, 3, 4]).all()
    assert len(out) < len(good)
