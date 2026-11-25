"""Small SDK bridge invoked by Airflow in a separate, pinned Python environment."""

import argparse
import json
import os
from pathlib import Path

from kfp import Client

PACKAGE = Path(__file__).with_name("compiled_pipeline.yaml")


def submit(client, request, snapshot, strict_gate, git_sha):
    name = "credit-risk-" + request
    experiment = client.create_experiment(name="credit-risk-orchestration")
    # Reconcile a previous accepted POST if Airflow restarted before persisting the run ID.
    query = {"predicates": [{"operation": 1, "key": "display_name", "stringValue": name}]}
    matches = (
        client.list_runs(
            experiment_id=experiment.experiment_id, filter=json.dumps(query), page_size=2
        ).runs
        or []
    )
    if len(matches) > 1:
        raise RuntimeError(f"Multiple KFP runs already exist for {request}")
    if matches:
        return matches[0].run_id
    run = client.create_run_from_pipeline_package(
        pipeline_file=str(PACKAGE),
        experiment_id=experiment.experiment_id,
        run_name=name,
        arguments={
            "request": request,
            "snapshot": json.dumps(snapshot),
            "strict_gate": int(strict_gate),
            "git_sha": git_sha,
        },
        enable_caching=False,
    )
    return run.run_id


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(value)
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["submit", "poll"])
    parser.add_argument("work", type=Path)
    parser.add_argument("--strict-gate", action="store_true")
    args = parser.parse_args()
    client = Client(host=os.environ["PHASE5_KFP_HOST"])
    run_file = args.work / "kfp-run-id.txt"
    if args.action == "submit":
        if not run_file.exists():
            run_id = submit(
                client,
                args.work.name,
                json.loads((args.work / "snapshot.json").read_text()),
                args.strict_gate,
                os.environ["PHASE5_BUILD_GIT_SHA"],
            )
            save(run_file, run_id)
        print("KFP run ID:", run_file.read_text())
    else:
        run = client.get_run(run_file.read_text().strip())
        save(args.work / "kfp-state.txt", run.state)
        print("KFP run", run.run_id, run.state)


if __name__ == "__main__":
    main()
