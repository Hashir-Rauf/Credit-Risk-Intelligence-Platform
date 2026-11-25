"""Data tasks executed with the ML interpreter outside Airflow's dependency environment."""

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from src.common.config import load_params, repo_root
from src.common.io import sha256_file
from src.common.seed import derive_seed
from src.data import ingestion, synthetic, validation

from pipelines.phase5.snapshot import publish


def fetch(work, mode):
    params = load_params()
    csv = work / "raw.csv"
    temp = work / "fetch.tmp"
    if mode == "uci":
        params["data"]["raw_path"] = str(temp)
        ingestion.ingest(params, force=True)
    elif mode == "s3":
        import boto3

        location = urlsplit(os.environ["PHASE5_SOURCE_S3"])
        if location.scheme != "s3" or not location.netloc or not location.path.strip("/"):
            raise ValueError("PHASE5_SOURCE_S3 must identify an S3 CSV object.")
        boto3.client("s3").download_file(location.netloc, location.path[1:], str(temp))
    elif mode == "synthetic":
        frame = synthetic.make_synthetic_credit_data(
            params["smoke"]["n_rows"], derive_seed(params["seed"], "synthetic")
        )
        frame.to_csv(temp, index=False, lineterminator="\n")
    elif mode == "corrupt":
        shutil.copyfile(repo_root() / "tests/fixtures/credit_corrupt.csv", temp)
    else:
        raise ValueError(f"Unknown input mode {mode}")
    temp.replace(csv)


def validate(work):
    csv = work / "raw.csv"
    try:
        result = validation.validate(pd.read_csv(csv))
    except validation.DataValidationError as error:
        (work / "validation.json").write_text(json.dumps(error.report))
        raise
    result["sha256"] = sha256_file(csv)
    (work / "validation.json").write_text(json.dumps(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fetch", "validate", "push", "gate"])
    parser.add_argument("work", type=Path)
    parser.add_argument("mode", choices=["uci", "s3", "synthetic", "corrupt"])
    args = parser.parse_args()
    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    if args.action == "fetch":
        fetch(work, args.mode)
    elif args.action == "validate":
        validate(work)
    elif args.action == "push":
        descriptor = publish(work / "raw.csv", work / "validation.json", args.mode)
        (work / "snapshot.json").write_text(json.dumps(descriptor))
    else:
        from pipelines.phase5.result import check_result

        check_result(work.name, args.mode == "synthetic")


if __name__ == "__main__":
    main()
