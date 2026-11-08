"""Download the UCI "Default of Credit Card Clients" dataset into data/raw/.

Source: https://archive.ics.uci.edu/dataset/350 (CC BY 4.0, Yeh 2009).

Idempotent and verified: the downloaded zip must match the SHA-256 pinned in
params.yaml, and the output CSV is written deterministically so identical input
always yields byte-identical output (important for DVC hashes).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import zipfile
from pathlib import Path

import pandas as pd
import requests
import yaml

log = logging.getLogger(__name__)

# UCI column -> project column. PAY_0 is really the first repayment-status column
# (the source skips PAY_1), so it is renamed to keep PAY_1..PAY_6 consistent.
COLUMN_RENAMES = {
    "ID": "customer_id",
    "PAY_0": "PAY_1",
    "default payment next month": "default",
}


class ChecksumError(RuntimeError):
    """The downloaded file does not match the pinned checksum."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_zip(url: str, expected_sha256: str, timeout: int = 60) -> bytes:
    log.info("Downloading %s", url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    actual = sha256_bytes(resp.content)
    if actual != expected_sha256:
        raise ChecksumError(f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {actual}")
    return resp.content


def zip_to_frame(content: bytes, excel_name: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf, zf.open(excel_name) as fh:
        df = pd.read_excel(fh, header=1)  # row 0 is a title row
    return df.rename(columns=COLUMN_RENAMES)


def apply_row_filters(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Optional, off by default. They create alternative data versions (used by the DVC
    reproducibility and merge-conflict exercises); any modelling use is decided in Phase 2."""
    if cfg.get("drop_duplicate_rows", False):
        df = df.drop_duplicates(subset=[c for c in df.columns if c != "customer_id"])
    if cfg.get("drop_unknown_education", False):
        df = df[df["EDUCATION"].isin([1, 2, 3, 4])]
    return df.reset_index(drop=True)


def ingest(params: dict, force: bool = False) -> Path:
    cfg = params["data"]
    out = Path(cfg["raw_path"])
    if out.exists() and not force:
        log.info("%s already exists (sha256 %s); use --force to rebuild", out, sha256_file(out))
        return out
    content = download_zip(cfg["source_url"], cfg["source_sha256"])
    df = apply_row_filters(zip_to_frame(content, cfg["excel_name"]), cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, lineterminator="\n")
    log.info("Wrote %s rows x %s cols to %s (sha256 %s)", *df.shape, out, sha256_file(out))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", default="params.yaml")
    parser.add_argument("--force", action="store_true", help="re-download even if output exists")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with open(args.params, encoding="utf-8") as fh:
        params = yaml.safe_load(fh)
    ingest(params, force=args.force)


if __name__ == "__main__":
    main()
