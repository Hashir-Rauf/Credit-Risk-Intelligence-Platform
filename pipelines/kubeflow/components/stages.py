"""Four native container components. The image already includes the project's ML stack."""

import os

from kfp import dsl

IMAGE = os.environ.get("PHASE5_COMPONENT_IMAGE", "credit-risk-phase5:local")
COMMAND = ["python", "-m", "pipelines.phase5.runtime"]


@dsl.container_component
def preprocess(snapshot: str, dataset: dsl.Output[dsl.Dataset]):
    return dsl.ContainerSpec(
        image=IMAGE,
        command=COMMAND,
        args=["preprocess", "--snapshot", snapshot, "--output", dataset.path],
    )


@dsl.container_component
def train_tune(dataset: dsl.Input[dsl.Dataset], git_sha: str, model: dsl.Output[dsl.Model]):
    return dsl.ContainerSpec(
        image=IMAGE,
        command=COMMAND,
        args=["train", "--dataset", dataset.path, "--git-sha", git_sha, "--output", model.path],
    )


@dsl.container_component
def evaluate(
    dataset: dsl.Input[dsl.Dataset],
    model: dsl.Input[dsl.Model],
    request: str,
    strict_gate: int,
    metrics: dsl.Output[dsl.Metrics],
    passed: dsl.OutputPath(bool),
):
    return dsl.ContainerSpec(
        image=IMAGE,
        command=COMMAND,
        args=[
            "evaluate",
            "--dataset",
            dataset.path,
            "--model",
            model.path,
            "--request",
            request,
            "--strict-gate",
            strict_gate,
            "--output",
            metrics.path,
            "--passed",
            passed,
            "--metrics-uri",
            metrics.uri,
            "--executor-output",
            dsl.PIPELINE_TASK_EXECUTOR_OUTPUT_PATH_PLACEHOLDER,
        ],
    )


@dsl.container_component
def package_register(
    model: dsl.Input[dsl.Model],
    metrics: dsl.Input[dsl.Metrics],
    registration: dsl.Output[dsl.Artifact],
):
    return dsl.ContainerSpec(
        image=IMAGE,
        command=COMMAND,
        args=[
            "register",
            "--model",
            model.path,
            "--metrics",
            metrics.path,
            "--output",
            registration.path,
        ],
    )
