"""Compile with python -m pipelines.kubeflow.pipeline_definition."""

from pathlib import Path

from kfp import compiler, dsl, kubernetes

from pipelines.kubeflow.components import stages

PACKAGE = Path(__file__).with_name("compiled_pipeline.yaml")
MLFLOW = ["MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD"]
DVC = ["PHASE5_DVC_URL", "PHASE5_DVC_USER", "PHASE5_DVC_PASSWORD"]


def configure(task, keys):
    task.set_caching_options(False)
    kubernetes.set_image_pull_policy(task, "IfNotPresent")
    kubernetes.use_secret_as_env(task, "credit-risk-phase5", {key: key for key in keys})
    return task


@dsl.pipeline(name="credit-risk-phase5", description="Validated snapshot to a gated staging model")
def credit_risk(snapshot: str, request: str, git_sha: str, strict_gate: int = 0):
    prepared = configure(stages.preprocess(snapshot=snapshot), DVC)
    trained = configure(stages.train_tune(dataset=prepared.output, git_sha=git_sha), MLFLOW)
    evaluated = configure(
        stages.evaluate(
            dataset=prepared.output, model=trained.output, request=request, strict_gate=strict_gate
        ),
        MLFLOW,
    )
    with dsl.If(evaluated.outputs["passed"] == True, name="quality-gate-passed"):  # noqa: E712
        configure(
            stages.package_register(model=trained.output, metrics=evaluated.outputs["metrics"]),
            MLFLOW,
        )


if __name__ == "__main__":
    compiler.Compiler().compile(credit_risk, str(PACKAGE))
