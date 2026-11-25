"""Entrypoints executed by KFP container components, one fresh filesystem per task."""

import argparse
import json
import os
from contextlib import suppress
from pathlib import Path

import yaml
from src.common.config import load_params, repo_root
from src.common.errors import GateFailedError
from src.common.io import atomic_write_bytes, read_json, write_json
from src.data import paths

from pipelines.phase5.bundles import pack, unpack
from pipelines.phase5.snapshot import retrieve


def prepare(params, snapshot, output):
    from src.features import feast_features, preprocessing

    smoke = snapshot["mode"] == "synthetic"
    csv = paths.raw_csv_path(params, smoke)
    retrieve(snapshot, csv)
    provenance = paths.provenance_path(params, smoke)
    write_json(provenance, {"source": snapshot["mode"], "sha256": snapshot["sha256"]})
    report = paths.validation_report_path(params, smoke)
    write_json(report, snapshot["validation"])
    preprocessing.run(params, smoke)
    feast_features.run(params, smoke)
    descriptor = repo_root() / "reports/phase5-input.json"
    write_json(descriptor, snapshot)
    pack(
        output,
        [
            csv,
            provenance,
            report,
            paths.feature_schema_path(params, smoke),
            paths.processed_dir(params, smoke),
            paths.feast_dir(params, smoke),
            descriptor,
        ],
    )


def train_model(params, dataset, output):
    from src.models import train, tune

    unpack(dataset)
    snapshot = read_json(repo_root() / "reports/phase5-input.json")
    smoke = snapshot["mode"] == "synthetic"
    # The existing MLflow provenance helper reads this key in each isolated training pod.
    lock_path = repo_root() / "dvc.lock"
    lock = yaml.safe_load(lock_path.read_text())
    raw = next(
        o for o in lock["stages"]["ingest"]["outs"] if o["path"] == params["data"]["raw_path"]
    )
    raw.update(md5=snapshot["md5"], size=snapshot["size"])
    atomic_write_bytes(lock_path, yaml.safe_dump(lock).encode())
    tune.run(params, smoke)
    train.run(params, smoke)
    pack(
        output,
        [
            paths.model_path(params, smoke),
            paths.metrics_path(params, smoke),
            paths.last_run_path(params, smoke),
            paths.feature_schema_path(params, smoke),
        ],
    )


def evaluate_model(params, args):
    from mlflow.tracking import MlflowClient
    from src.models import evaluate

    unpack(args.dataset)
    unpack(args.model)
    snapshot = read_json(repo_root() / "reports/phase5-input.json")
    smoke = snapshot["mode"] == "synthetic"
    if args.strict_gate:
        params["gate"]["roc_auc_min"] = 1.0
    # A rejected gate is an output of this component; infrastructure failures propagate.
    with suppress(GateFailedError):
        evaluate.run(params, smoke)
    report = read_json(paths.evaluation_path(params, smoke))
    gate = report["gate"]
    run_id = read_json(paths.last_run_path(params, smoke))["run_id"]
    client = MlflowClient()
    client.set_tag(run_id, "phase5_request", args.request)
    client.set_tag(run_id, "gate_passed", str(gate["passed"]).lower())
    client.set_tag(run_id, "phase5_input_md5", snapshot["md5"])
    client.log_artifact(run_id, str(paths.evaluation_path(params, smoke)), "phase5")
    write_json(args.output, {**report, "run_id": run_id, "smoke": smoke})
    atomic_write_bytes(args.passed, str(gate["passed"]).lower().encode())
    if args.executor_output:
        numeric = {k: v for k, v in report["test_metrics"].items() if isinstance(v, (int, float))}
        write_json(
            args.executor_output,
            {
                "artifacts": {
                    "metrics": {
                        "artifacts": [
                            {"name": "metrics", "uri": args.metrics_uri, "metadata": numeric}
                        ]
                    }
                },
                "parameterValues": {"passed": gate["passed"]},
            },
        )


def package(params, model, metrics, output):
    from mlflow.tracking import MlflowClient
    from src.models.registry import register_candidate

    from pipelines.phase5.oci import build_image

    unpack(model)
    report = read_json(metrics)
    smoke = report["smoke"]
    run_id = report["run_id"]
    if (
        not report["gate"]["passed"]
        or read_json(paths.last_run_path(params, smoke))["run_id"] != run_id
    ):
        raise ValueError("Registration requires a passing evaluation for this training run")
    write_json(paths.evaluation_path(params, smoke), report)
    files = {
        "model.joblib": paths.model_path(params, smoke),
        "feature_schema.json": paths.feature_schema_path(params, smoke),
        "metrics.json": paths.metrics_path(params, smoke),
        "evaluation.json": metrics,
    }
    image = repo_root() / "reports/model-image.oci.tar"
    digest = build_image(image, files, run_id)
    client = MlflowClient()
    client.log_artifact(run_id, str(image), "phase5")
    params["mlflow"]["challenger_alias"] = "staging"
    record = register_candidate(params, smoke)
    client.set_model_version_tag(
        record["model_name"], record["version"], "phase5_oci_digest", digest
    )
    client.set_tag(run_id, "phase5_staging_version", record["version"])
    write_json(
        output, {**record, "image_digest": digest, "image_artifact": "phase5/model-image.oci.tar"}
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preprocess", "train", "evaluate", "register"])
    for name in ("dataset", "model", "metrics", "output", "passed", "executor-output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--snapshot")
    parser.add_argument("--metrics-uri")
    parser.add_argument("--request")
    parser.add_argument("--git-sha")
    parser.add_argument("--strict-gate", type=int, choices=[0, 1], default=0)
    args = parser.parse_args()
    params = load_params()
    if args.git_sha:
        os.environ["GIT_COMMIT_SHA"] = args.git_sha
    if args.action != "preprocess" and not os.environ.get("MLFLOW_TRACKING_URI"):
        raise ValueError("Missing shared MLFLOW_TRACKING_URI in runtime secret")
    if args.action == "preprocess":
        prepare(params, json.loads(args.snapshot), args.output)
    elif args.action == "train":
        train_model(params, args.dataset, args.output)
    elif args.action == "evaluate":
        evaluate_model(params, args)
    else:
        package(params, args.model, args.metrics, args.output)


if __name__ == "__main__":
    main()
