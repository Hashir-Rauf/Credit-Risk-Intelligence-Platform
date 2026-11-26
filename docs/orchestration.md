# Epic 5 orchestration

Start with docs/kubeflow-setup.md. Airflow owns ingestion, validation, DVC publication, scheduling and notification. Kubeflow owns the pod graph that produces the MLflow model version. A run starts from one Airflow trigger.

## Trigger the first run

Open the Airflow UI at http://localhost:8085. Unpause ml_orchestrator_dag, choose Trigger DAG and select source=synthetic for a small first run. Leave strict_gate and retry_demo false. The weekly schedule uses source=uci. With catchup disabled, unpausing can schedule the latest eligible weekly interval. Use the logical run ID in the logs to distinguish manual and scheduled runs.

The Airflow task order is fetch_data → validate → dvc_push → trigger_kfp_run → poll_run → check_gate → notify. The DAG allows one active run. Task retries use a two-minute delay; the polling sensor releases its worker slot between checks, has a six-hour deadline, and the DAG has an eight-hour timeout. Airflow tracks the six-hour SLA for scheduled work. Task failures invoke the configured callback. Notifications appear in the Airflow logs; set PHASE5_NOTIFY_WEBHOOK before startup for an HTTP receiver accepting a JSON text field.

| KFP task | Inputs | Outputs |
|---|---|---|
| preprocess | Exact DVC MD5, expected SHA-256, validation report and source mode | Dataset artifact containing raw snapshot, provenance, three splits, feature schema, and portable Feast state |
| train_tune | Dataset and source revision | Model artifact containing the fitted pipeline, feature schema, recorded metrics and MLflow run/model URI |
| evaluate | Dataset and model from the same KFP run | Typed Metrics artifact, evaluation JSON, numeric metric metadata, MLflow tags and Boolean gate result |
| package_register | Passing Metrics and Model artifacts | OCI model init-container image on the MLflow run, staging model version and registration receipt |

Each worker restores the artifact to the same /app-relative locations used when Feast's registry was created. Every task has an independent filesystem. KFP manages storage between pods. Credentials stay out of snapshot parameters and XCom. The DVC remote contains the content-addressed validated CSV. SHA-256 is checked after retrieval. Each pod trains or evaluates using the model code and thresholds from the ML image.

Training already serializes an MLflow sklearn model with its feature signature, preprocessing and model URI. After evaluation the packaging task builds a Linux OCI image layout as an OCI archive with a statically linked installer, model.joblib, feature schema, test metrics and evaluation report. Its entrypoint copies the files to a mounted writable directory (default /output). This is an init-container payload for a serving workload. The existing Flask service supplies the HTTP API. The OCI digest is recorded on the MLflow model version. The image archive is available under phase5/model-image.oci.tar in the training run; a registry push or serving rollout belongs to the deployment phase.

The gate defaults to params.yaml. For the failed-gate demonstration, strict_gate raises the threshold to 1.0. The package task is conditional on the Boolean pass output. Airflow checks MLflow for the specific orchestration request and version after KFP succeeds. A rejected model can therefore yield a successful KFP evaluation and a failed Airflow check_gate task. Registration also verifies run identity and metrics internally. Automated registration sets staging; champion promotion is a separate governance decision.

A stable hash of the Airflow run ID identifies the workspace and KFP submission. Retries reuse the recorded run ID or look up the same named run in the experiment. A task rejects ambiguous matches. A staging retry reuses the existing model version for a tagged training run. Independent Airflow runs use distinct work directories.

## Acceptance runs and evidence

Use the DAG trigger form once per row. Keep the Airflow run ID, KFP run ID, MLflow run ID, model version, graph screenshots and relevant logs in docs/evidence/phase-5. Use the real-data run for reported metrics. Synthetic runs use the smoke experiment and registry model.

| Trigger parameters | Expected result |
|---|---|
| source=uci | Validated 30,000-row snapshot reaches KFP, gate passes for the verified model, staging registration and notification succeed |
| source=corrupt | The repository corrupt fixture fails validation (helper exit code 2). The task fails on its first attempt without retries; DVC push and KFP submission stay upstream_failed. |
| source=synthetic, strict_gate=true | Test ROC-AUC is below 1.0 for this fixture. KFP skips package_register, check_gate fails on its first attempt (helper exit code 3, no retries), notify stays upstream_failed, and the registry receives no new version. The same happens with source=uci when the real model misses a strict threshold. |
| source=synthetic, retry_demo=true | The first fetch attempt raises a deliberate transient failure. Its second attempt succeeds and one KFP run is submitted. |

For S3 ingestion select source=s3 and set PHASE5_SOURCE_S3 to an authorized normalized CSV object. Set the AWS region and a short-lived AWS credential set in the shell used by Compose. UCI ingestion and DagsHub DVC need no AWS credentials. External drift detection can trigger the same DAG using Airflow's authenticated DAG run API.

## Verify the code

The compiler and Airflow dependencies are intentionally isolated from the ML lock. Use the provided Docker images for execution. In separate Python environments, the tests can be run as follows from the project root.

    python -m pytest pipelines/tests/test_snapshot.py
    python -m pytest pipelines/tests/test_graph.py
    python -m pytest pipelines/tests/test_dag.py

Use the project ML environment for test_snapshot, an environment with pipelines/kubeflow/requirements.txt for test_graph and apache-airflow==2.10.5 for test_dag. Each environment needs pytest. Regenerate the checked-in default IR with python -m pipelines.kubeflow.pipeline_definition in the compiler environment. The Airflow image compiles the identical graph with the exact image tag selected for the build.

## Troubleshooting

If a component cannot start, inspect its pod events in kubeflow. An image pull error for credit-risk-phase5 means the local node lacks the tag embedded in the submitted IR. Start reloads the exact image. An authorization error in preprocess usually means the pod secret or DVC remote differs from the Airflow credentials. A failed poll reports the KFP run ID. Inspect that run's first failed component in the KFP UI. A successful check_gate log identifies the staging model version and training run.

Deterministic outcomes are not retried. The helper exits with 2 for failed data validation and 3 for a rejected quality gate, the codes defined in docs/contracts-s1.md, and the DAG turns both into a non-retryable failure. Any other non-zero exit, such as a network error, uses the normal two retries. The helper's own output appears in the task log, prefixed with the helper name.

A separate minikube profile keeps an existing cluster untouched: start it with `minikube start -p credit-risk --driver=docker` and run `scripts/phase5.ps1 -Action Start -Profile credit-risk`. If Docker restarts, run `minikube start -p credit-risk` again and then the Start action. Runs that were in progress stay `running` in Airflow after a restart and have to be failed by hand.

Two Airflow installations that share one MLflow server stay distinguishable because the request ID hashes the run ID together with the DagRun start time. The first live acceptance run exposed this: a scheduled run ID is identical on every installation.
