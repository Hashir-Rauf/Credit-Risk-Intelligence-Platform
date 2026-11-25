"""Offline workflow acceptance test inside the component image with private temp state."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="phase5-acceptance-") as temp:
        root = Path(temp)
        app = root / "app"
        app.mkdir()
        for name in ("src", "configs", "feature_repo", "pipelines"):
            shutil.copytree(ROOT / name, app / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("params.yaml", "dvc.lock"):
            shutil.copyfile(ROOT / name, app / name)
        fixtures = app / "tests/fixtures"
        fixtures.mkdir(parents=True)
        shutil.copyfile(ROOT / "tests/fixtures/credit_corrupt.csv", fixtures / "credit_corrupt.csv")
        env = dict(
            os.environ,
            MLFLOW_TRACKING_URI=f"sqlite:///{(root / 'mlflow.db').as_posix()}",
            PHASE5_DVC_URL=str(root / "remote"),
            PYTHONPATH=str(app),
            DVC_NO_ANALYTICS="true",
        )
        env["MLFLOW_REGISTRY_URI"] = env["MLFLOW_TRACKING_URI"]
        (root / "remote").mkdir()
        request = "12345678123456781234567812345678"
        work = root / request

        def run(module, *args, expected_error=None):
            cmd = [sys.executable, "-m", "pipelines.phase5." + module, *map(str, args)]
            result = subprocess.run(  # noqa: S603
                cmd,
                cwd=app,
                env=env,
                capture_output=bool(expected_error),
                text=True,
                check=expected_error is None,
            )
            if expected_error and (not result.returncode or expected_error not in result.stderr):
                raise RuntimeError(f"Expected {expected_error!r}; got {result.stderr}")

        def fresh_pod():
            for name in ("data", "reports", "models"):
                if (app / name).exists():
                    shutil.rmtree(app / name)

        for action in ("fetch", "validate", "push"):
            run("airflow_data", action, work, "synthetic")
        dataset, model = root / "dataset.zip", root / "model.zip"
        metrics, registration = root / "metrics.json", root / "registration.json"
        run(
            "runtime",
            "preprocess",
            "--snapshot",
            (work / "snapshot.json").read_text(),
            "--output",
            dataset,
        )
        fresh_pod()
        run(
            "runtime",
            "train",
            "--dataset",
            dataset,
            "--git-sha",
            "isolated-smoke",
            "--output",
            model,
        )
        fresh_pod()

        def evaluate(strict):
            run(
                "runtime",
                "evaluate",
                "--dataset",
                dataset,
                "--model",
                model,
                "--request",
                request,
                "--strict-gate",
                strict,
                "--output",
                metrics,
                "--passed",
                root / "gate",
                "--metrics-uri",
                "local-test://evaluation",
                "--executor-output",
                root / "executor.json",
            )

        evaluate(0)
        fresh_pod()
        run("runtime", "register", "--model", model, "--metrics", metrics, "--output", registration)
        first = json.loads(registration.read_text())
        run("runtime", "register", "--model", model, "--metrics", metrics, "--output", registration)
        if first != json.loads(registration.read_text()) or first["alias"] != "staging":
            raise RuntimeError("Registration retry changed the evaluated model version")
        run("airflow_data", "gate", work, "synthetic")
        evaluate(1)
        run(
            "runtime",
            "register",
            "--model",
            model,
            "--metrics",
            metrics,
            "--output",
            registration,
            expected_error="requires a passing evaluation",
        )
        run("airflow_data", "gate", work, "synthetic", expected_error="Quality gate rejected")
        run("airflow_data", "fetch", work, "corrupt")
        run("airflow_data", "validate", work, "corrupt", expected_error="DataValidationError")
        print("PASS: DVC, artifact handoff, staging, retry, rejected gate and corrupt input.")
        print(json.dumps(first, indent=2))


if __name__ == "__main__":
    main()
