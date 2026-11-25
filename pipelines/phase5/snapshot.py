"""Exchange the exact validated CSV by content hash using a per-operation DVC repository."""

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from src.common.io import sha256_file


def dvc(directory, *args):
    subprocess.run([sys.executable, "-m", "dvc", *args], cwd=directory, check=True)  # noqa: S603


def configure(directory):
    dvc(directory, "init", "--no-scm")
    url = os.environ["PHASE5_DVC_URL"]
    config = configparser.ConfigParser(interpolation=None)
    config['remote "snapshots"'] = {"url": url}
    if url.startswith("https://"):
        remote = config['remote "snapshots"']
        remote.update(
            auth="basic",
            user=os.environ["PHASE5_DVC_USER"],
            password=os.environ["PHASE5_DVC_PASSWORD"],
        )
    with (directory / ".dvc/config.local").open("w") as stream:
        config.write(stream)


def publish(csv, validation_file, mode):
    validation = json.loads(validation_file.read_text())
    sha = sha256_file(csv)
    if validation["passed"] is not True or validation["sha256"] != sha:
        raise ValueError("The CSV differs from the validated input.")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        configure(root)
        shutil.copyfile(csv, root / "raw.csv")
        dvc(root, "add", "raw.csv")
        out = yaml.safe_load((root / "raw.csv.dvc").read_text())["outs"][0]
        dvc(root, "push", "-r", "snapshots", "raw.csv.dvc")
    return {
        "md5": out["md5"],
        "sha256": sha,
        "size": csv.stat().st_size,
        "validation": validation,
        "mode": mode,
    }


def retrieve(snapshot, target):
    if not re.fullmatch(r"[a-f0-9]{32}", snapshot["md5"]):
        raise ValueError("Invalid DVC object hash")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        configure(root)
        out = {"md5": snapshot["md5"], "size": snapshot["size"], "hash": "md5", "path": "raw.csv"}
        (root / "raw.csv.dvc").write_text(yaml.safe_dump({"outs": [out]}))
        dvc(root, "pull", "-r", "snapshots", "raw.csv.dvc")
        actual = sha256_file(root / "raw.csv")
        report = snapshot["validation"]
        if (
            report["passed"] is not True
            or actual != snapshot["sha256"]
            or actual != report["sha256"]
        ):
            raise ValueError("DVC artifact does not match the validated SHA-256.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "raw.csv", target)
