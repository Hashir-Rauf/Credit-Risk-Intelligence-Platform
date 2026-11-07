"""Fetch the raw credit-card data and write it as one canonical CSV.

    python -m src.data.ingestion [--params params.yaml] [--smoke]

Sources (params.yaml `data.source`): uci, s3, local, synthetic. Only the *header* is
normalised (UCI `PAY_0` -> `PAY_1`, target -> `default_next_month`); values are untouched,
so validation sees exactly what the source contained. `--smoke` always uses synthetic data
under `smoke.data_dir` and can never overwrite DVC-tracked `data/raw`.
"""

from __future__ import annotations

import io
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from src.common.cli import stage_main
from src.common.config import resolve_path
from src.common.errors import ConfigError, IngestionError
from src.common.io import atomic_write_bytes, sha256_file, write_json
from src.common.logging_utils import get_logger
from src.common.seed import derive_seed
from src.data.paths import ingestion_report_path, raw_csv_path
from src.data.schema import BILL_AMT_COLUMNS, PAY_AMT_COLUMNS, PAY_COLUMNS, canonical_columns
from src.data.synthetic import make_synthetic_credit_data

log = get_logger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60
_TABLE_SUFFIXES = (".csv", ".xls")


# ------------------------------------------------------------------ header normalisation


def _key(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _alias_map(params: dict[str, Any]) -> dict[str, str]:
    """Known header spellings (UCI xls, Kaggle csv, canonical) -> canonical name."""
    data = params["data"]
    aliases = {
        "id": data["id_column"],
        data["id_column"].lower(): data["id_column"],
        "default_payment_next_month": data["target_column"],
        data["target_column"].lower(): data["target_column"],
        "limit_bal": "LIMIT_BAL",
        "sex": "SEX",
        "education": "EDUCATION",
        "marriage": "MARRIAGE",
        "age": "AGE",
        "pay_0": "PAY_1",  # UCI names the first repayment-status column PAY_0
    }
    for name in [*PAY_COLUMNS, *BILL_AMT_COLUMNS, *PAY_AMT_COLUMNS]:
        aliases[_key(name)] = name
    return aliases


def normalize_columns(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Rename recognised headers to canonical names; unknown columns are left for validation
    to report. Integral float columns become int64 so dtypes do not depend on the source."""
    aliases = _alias_map(params)
    renamed = [aliases.get(_key(col), str(col).strip()) for col in df.columns]
    duplicates = sorted({c for c in renamed if renamed.count(c) > 1})
    if duplicates:
        raise IngestionError(f"source has duplicate columns after normalisation: {duplicates}")
    out = df.copy()
    out.columns = renamed

    for col in out.columns:
        series = out[col]
        if pd.api.types.is_float_dtype(series) and series.notna().all():
            if (series == series.round()).all():
                out[col] = series.astype("int64")
    return out


# ------------------------------------------------------------------ readers


def _read_table(content: bytes, name: str) -> pd.DataFrame:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        try:
            return pd.read_csv(io.BytesIO(content))
        except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
            raise IngestionError(f"cannot parse CSV {name}: {exc}") from exc
    if suffix == ".xls":
        try:
            preview = pd.read_excel(io.BytesIO(content), header=None, nrows=5, engine="calamine")
        except Exception as exc:
            raise IngestionError(f"cannot read Excel file {name}: {exc}") from exc
        # The UCI sheet has a "X1..X23,Y" label row above the real header row.
        header_row = next(
            (i for i, row in preview.iterrows() if any(_key(v) == "limit_bal" for v in row)), None
        )
        if header_row is None:
            raise IngestionError(f"no header row containing LIMIT_BAL found in {name}")
        return pd.read_excel(io.BytesIO(content), header=int(header_row), engine="calamine")
    raise IngestionError(f"unsupported source file type '{suffix}' ({name}); use .csv or .xls")


def _pick_table_in_zip(content: bytes) -> tuple[bytes, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [m for m in archive.namelist() if m.lower().endswith(_TABLE_SUFFIXES)]
            if not members:
                raise IngestionError(
                    f"zip contains no {_TABLE_SUFFIXES} file: {archive.namelist()}"
                )
            member = sorted(members)[0]
            return archive.read(member), member
    except zipfile.BadZipFile as exc:
        raise IngestionError("downloaded file is not a valid zip archive") from exc


def _download(url: str) -> bytes:
    if urlparse(url).scheme != "https":
        raise ConfigError("data.uci_url must use https")
    request = urllib.request.Request(url, headers={"User-Agent": "credit-risk-platform-ingestion"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
            content = response.read(MAX_DOWNLOAD_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IngestionError(f"download failed for {url}: {exc}") from exc
    if len(content) > MAX_DOWNLOAD_BYTES:
        raise IngestionError(f"download exceeds {MAX_DOWNLOAD_BYTES} bytes; refusing to continue")
    return content


def _fetch_s3(params: dict[str, Any]) -> tuple[bytes, str]:
    env_name = params["data"]["s3_uri_env"]
    uri = os.environ.get(env_name, "")
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ConfigError(f"environment variable {env_name} must hold s3://bucket/key")
    key = parsed.path.lstrip("/")
    try:
        import boto3  # lazy: only needed for the s3 source

        buffer = io.BytesIO()
        boto3.client("s3").download_fileobj(parsed.netloc, key, buffer)
    except ImportError as exc:
        raise IngestionError("boto3 is required for data.source=s3") from exc
    except Exception as exc:  # botocore errors (credentials, missing key, access denied)
        raise IngestionError(f"S3 download failed for s3://{parsed.netloc}/{key}: {exc}") from exc
    return buffer.getvalue(), key


def load_source(params: dict[str, Any], smoke: bool) -> tuple[pd.DataFrame, str]:
    """Return (raw DataFrame with source headers, source label)."""
    data = params["data"]
    source = "synthetic" if smoke else data["source"]

    if source == "synthetic":
        n_rows = params["smoke"]["n_rows"] if smoke else data["synthetic_rows"]
        log.warning("USING SYNTHETIC DATA (%d rows): demos/tests only, never for results", n_rows)
        frame = make_synthetic_credit_data(
            n_rows,
            derive_seed(params["seed"], "synthetic"),
            id_column=data["id_column"],
            target_column=data["target_column"],
        )
        return frame, "synthetic"

    if source == "uci":
        content, member = _pick_table_in_zip(_download(data["uci_url"]))
        return _read_table(content, member), "uci"

    if source == "s3":
        content, key = _fetch_s3(params)
        if key.lower().endswith(".zip"):
            content, key = _pick_table_in_zip(content)
        return _read_table(content, key), "s3"

    if source == "local":
        path = resolve_path(data["local_path"])
        if not path.is_file():
            raise IngestionError(f"data.local_path not found: {path}")
        return _read_table(path.read_bytes(), path.name), "local"

    raise ConfigError(f"unsupported data.source {source!r}")


# ------------------------------------------------------------------ stage


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    raw, source = load_source(params, smoke)
    frame = normalize_columns(raw, params)

    # Canonical columns first (in canonical order). Unknown extras are kept at the end and
    # missing columns are left missing: validation reports both precisely.
    expected = canonical_columns(params)
    present = [c for c in expected if c in frame.columns]
    frame = frame[[*present, *[c for c in frame.columns if c not in expected]]]

    out_path = raw_csv_path(params, smoke)
    atomic_write_bytes(out_path, frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))

    report = {
        "source": source,
        "synthetic": source == "synthetic",
        "rows": int(len(frame)),
        "columns": int(frame.shape[1]),
        "sha256": sha256_file(out_path),
        "file": out_path.name,
    }
    write_json(ingestion_report_path(params, smoke), report)
    log.info(
        "ingested %d rows x %d cols from %s -> %s", len(frame), frame.shape[1], source, out_path
    )
    return {"raw_csv": str(out_path), "rows": report["rows"], "source": source}


if __name__ == "__main__":
    raise SystemExit(stage_main(run, description="Fetch raw data into data/raw (canonical CSV)"))
