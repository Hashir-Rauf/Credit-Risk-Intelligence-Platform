"""Fetch the model files of a registered version from MLflow (used to deploy an approved champion).

    python -m pipelines.retrain.fetch_model --version 4 [--model-name credit-risk-default-model]

The Phase 5 pipeline stores, with every registered model, an OCI image layout (`phase5/model-image.oci.tar`) holding
`model.joblib`, `feature_schema.json`, `metrics.json` and `evaluation.json` of exactly the evaluated run. This script
reads those files out of the archive (no container runtime needed) and places them where the API image expects them,
plus `reports/registration.json` so that the build knows the model version. The explainer background is not part of
the package; the deploy workflow takes it from DVC.

Needs MLFLOW_TRACKING_URI (and credentials) in the environment.
"""

from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
from pathlib import Path

WANTED = {
    "model/model.joblib": "models/model.joblib",
    "model/feature_schema.json": "reports/feature_schema.json",
    "model/metrics.json": "reports/metrics.json",
    "model/evaluation.json": "reports/evaluation.json",
}


def extract(archive: Path, root: Path) -> list[str]:
    """Unpack the model files from an OCI image layout into `root`. Only the four expected names are written."""
    written = []
    with tarfile.open(archive) as image:

        def read(name: str) -> bytes:
            member = image.extractfile(name.lstrip("/"))
            if member is None:
                raise ValueError(f"{name} is missing from the image archive")
            return member.read()

        index = json.loads(read("index.json"))
        manifest = json.loads(read("blobs/" + index["manifests"][0]["digest"].replace(":", "/")))
        for layer in manifest["layers"]:
            data = read("blobs/" + layer["digest"].replace(":", "/"))
            with tempfile.TemporaryFile() as handle:
                handle.write(data)
                handle.seek(0)
                with tarfile.open(fileobj=handle) as files:
                    for member in files.getmembers():
                        target = WANTED.get(member.name)
                        if target is None or not member.isfile():
                            continue  # never trust other names (path traversal, the installer binary)
                        destination = root / target
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(files.extractfile(member).read())
                        written.append(target)
    missing = sorted(set(WANTED.values()) - set(written))
    if missing:
        raise ValueError(f"the model package is incomplete, missing: {', '.join(missing)}")
    return sorted(written)


def fetch(version: str, model_name: str, root: Path) -> dict[str, str]:
    from mlflow.artifacts import download_artifacts
    from mlflow.tracking import MlflowClient

    client = MlflowClient()
    model_version = client.get_model_version(model_name, version)
    archive = download_artifacts(
        run_id=model_version.run_id, artifact_path="phase5/model-image.oci.tar"
    )
    extract(Path(archive), root)
    record = {"model_name": model_name, "version": str(version), "run_id": model_version.run_id}
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports/registration.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--model-name", default="credit-risk-default-model")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(fetch(args.version, args.model_name, args.root)))


if __name__ == "__main__":
    main()
