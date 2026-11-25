# Phase 5 setup on Windows and Docker Desktop

Use PowerShell from the Credit Risk project root. This setup targets a single-node Minikube cluster using the Docker driver, the kubeflow namespace and KFP 2.18.2. Allocate at least 8 GB to Docker Desktop as specified in phases.md. Kubernetes uses the existing ML image on the local node. Airflow runs separately in Compose on the same Docker network. Keep local Docker Desktop, Minikube and kubectl running.

## 1. Build the component and Airflow images

    .\scripts\phase5.ps1 -Action Build

The script reuses credit-risk-ml:phase5 from the successful six-stage Docker test. On a machine without that image it builds a base from the project lockfile. A small derived image adds the orchestration modules and the model package helper. The Airflow image includes isolated ML and KFP Python environments. Run Build again after changing the components or DAG. Rebuild the ML base separately after changing src, params, feature definitions or the lockfile. For graded evidence commit the intended source before building so the SHA identifies those files.

Run the optional offline acceptance check from the root. It uses a temporary DVC remote, SQLite MLflow registry, synthetic data and independent stage workspaces inside the container.

    docker run --rm --entrypoint python credit-risk-phase5:local /app/scripts/phase5-local-smoke.py

## 2. Use the appropriate cluster setup

**Existing coursework cluster.** Check the live API and UI with the following command.

    .\scripts\phase5.ps1 -Action Status

If this is the cluster initialized by the dev overlay and the pinned API crashes with MySQL 1068 on transfer_identities, run the targeted recovery below. It selects a fresh pipeline database named mlpipeline_phase5_2182, aligns the API, driver, launcher and installed KFP master-tagged controllers with the release, and waits for rollout. Previous run history remains in the older database. The optional GCP proxy agent from the dev overlay is outside this local workflow. Its host-network admission warning can be tracked separately.

    .\scripts\phase5-repair-existing.ps1

If a rollout fails, inspect the newest pod for that deployment with kubectl logs --previous. Avoid applying the dev overlay again, since it restores master images. Record whether this recovery was used.

**Fresh cluster only.** Start the minikube Docker profile. The release renderer downloads two digest-checked archives into TEMP and rewrites Argo references to local files before invoking kubectl kustomize. The platform-agnostic overlay uses release images and omits the GCP proxy agent. Run these commands only in a fresh cluster. Check each exit status.

    minikube start -p minikube --driver=docker
    $kfp = Join-Path $env:TEMP 'credit-risk-phase5-release'
    py -3.11 scripts/phase5-kfp-manifests.py $kfp
    if ($LASTEXITCODE) { throw 'Release rendering failed.' }
    kubectl --context minikube apply -f "$kfp\cluster.yaml"
    if ($LASTEXITCODE) { throw 'Cluster resource creation failed.' }
    kubectl --context minikube wait --for=condition=Established crd/workflows.argoproj.io --timeout=120s
    if ($LASTEXITCODE) { throw 'Workflow CRD is still starting.' }
    kubectl --context minikube apply -f "$kfp\standalone.yaml"
    if ($LASTEXITCODE) { throw 'KFP installation failed.' }
    kubectl --context minikube -n kubeflow get pods,pvc

## 3. Configure the shared services

Use your project's DagsHub tracking server and HTTPS DVC remote. Start automatically reuses a complete project DVC credential pair when it finds the matching endpoint and credentials on this machine. To configure them explicitly, set the six environment variables below in the PowerShell session used to start Compose. These commands reuse the local credential pair from your verified project DVC configuration. Check the host/project before reusing it for tracking. For a separate MLflow account supply the appropriate MLflow credentials separately.

    $env:PHASE5_DVC_URL = (dvc config remote.dagshub.url)
    if ($LASTEXITCODE -or $env:PHASE5_DVC_URL -ne 'https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform.dvc') { throw 'Check the project DVC remote.' }
    $env:PHASE5_DVC_USER = (dvc config remote.dagshub.user)
    $env:PHASE5_DVC_PASSWORD = (dvc config remote.dagshub.password)
    $env:MLFLOW_TRACKING_URI = 'https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform.mlflow'
    $env:MLFLOW_TRACKING_USERNAME = $env:PHASE5_DVC_USER
    $env:MLFLOW_TRACKING_PASSWORD = $env:PHASE5_DVC_PASSWORD

If working from a source ZIP without .dvc/config.local, enter the same project URL explicitly and use PowerShell Get-Credential to populate the user and password variables for your own DagsHub account. Use an account authorized for DVC push and MLflow registration. Pipeline pods receive scoped keys from a Kubernetes Secret. Keep credentials out of logs and Git.

## 4. Start Airflow

    .\scripts\phase5.ps1 -Action Start
    docker compose -f pipelines/airflow/compose.yaml logs --tail=60 airflow

Start verifies KFP rollouts, loads the component image, configures the runtime secret and creates a local NodePort service on the Minikube Docker network for Airflow. This avoids a host port-forward dependency for scheduled runs. The single-user KFP endpoint is intended only for the local coursework cluster. Airflow is accessible at http://localhost:8085. The initial standalone username is admin. Retrieve its generated local password privately when needed.

    docker compose -f pipelines/airflow/compose.yaml exec airflow cat /opt/airflow/state/standalone_admin_password.txt

To view the KFP graph from Windows, in a separate terminal run the following and open http://localhost:8080.

    kubectl --context minikube -n kubeflow port-forward svc/ml-pipeline-ui 8080:80

Continue with docs/orchestration.md. Stop the local scheduler with docker compose -f pipelines/airflow/compose.yaml stop. Stateful Compose volumes retain run history across stops.
