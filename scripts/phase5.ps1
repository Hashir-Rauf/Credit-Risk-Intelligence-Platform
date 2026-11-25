[CmdletBinding()]
param(
    [ValidateSet('Build', 'Start', 'Status')]
    [string]$Action = 'Build'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$k = @('--context', 'minikube', '--namespace', 'kubeflow')
function Check([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)." }
}
function Status {
    kubectl @k get deployments mysql ml-pipeline ml-pipeline-ui workflow-controller
    Check 'KFP deployment lookup'
}
if ($Action -eq 'Status') { Status; return }

if ($Action -eq 'Build') {
    $revision = 'archive'
    if (Test-Path -LiteralPath '.git') {
        $revision = (git rev-parse HEAD)
        Check 'Git revision lookup'
    }
    docker image inspect credit-risk-ml:phase5 *> $null
    if ($LASTEXITCODE -ne 0) {
        docker build -f pipelines/docker/Dockerfile.base -t credit-risk-ml:phase5 .
        Check 'ML base build'
    }
    docker build -f pipelines/docker/Dockerfile.runtime -t credit-risk-phase5:local .
    Check 'KFP component image build'
    $imageId = (docker image inspect --format '{{.Id}}' credit-risk-phase5:local)
    Check 'Runtime image identity'
    $runtime = 'credit-risk-phase5:img-' + ($imageId -replace '^sha256:', '').Substring(0, 24)
    docker tag credit-risk-phase5:local $runtime
    Check 'Runtime image tag'
    docker build -f pipelines/docker/Dockerfile.airflow --build-arg "COMPONENT_IMAGE=$runtime" --build-arg "SOURCE_REVISION=$revision" -t credit-risk-airflow:phase5 .
    Check 'Airflow image build'
    docker run --rm --entrypoint /opt/venv/bin/python -e PYTHONNOUSERSITE=1 -w /app credit-risk-airflow:phase5 -c 'from pipelines.phase5 import airflow_data'
    Check 'ML interpreter in Airflow image'
    docker run --rm --entrypoint /opt/kfp/bin/python -e PYTHONNOUSERSITE=1 -w /app credit-risk-airflow:phase5 -c 'from pipelines.kubeflow import client'
    Check 'KFP interpreter in Airflow image'
    Write-Output "Built $runtime and credit-risk-airflow:phase5. Next run this script with -Action Start."
    return
}

foreach ($deployment in @('mysql', 'ml-pipeline', 'ml-pipeline-ui', 'workflow-controller')) {
    kubectl @k rollout status "deployment/$deployment" --timeout=90s
    Check "$deployment rollout"
}
$runtime = (docker image inspect --format '{{json .Config.Labels}}' credit-risk-airflow:phase5 | ConvertFrom-Json).'phase5.component'
Check 'Built Airflow image lookup'
if ($runtime -notmatch '^credit-risk-phase5:img-[0-9a-f]{24}$') { throw 'Run the Build action to generate a matching component image.' }
# Reuse a configured DagsHub pair only for the matching project endpoints.
$project = 'https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform'
$dvcExe = Get-Command dvc -ErrorAction SilentlyContinue
if (-not $dvcExe -and (Test-Path '.venv/Scripts/dvc.exe')) {
    $dvcExe = Get-Command '.venv/Scripts/dvc.exe'
}
if ($dvcExe -and (-not $env:PHASE5_DVC_URL -or -not $env:PHASE5_DVC_PASSWORD)) {
    $configured = & $dvcExe.Source config remote.dagshub.url
    if ($LASTEXITCODE -eq 0 -and $configured -eq "$project.dvc" -and
        (-not $env:PHASE5_DVC_URL -or $env:PHASE5_DVC_URL -eq $configured)) {
        $env:PHASE5_DVC_URL = $configured
        if (-not $env:PHASE5_DVC_USER -and -not $env:PHASE5_DVC_PASSWORD) {
            $env:PHASE5_DVC_USER = & $dvcExe.Source config remote.dagshub.user
            $env:PHASE5_DVC_PASSWORD = & $dvcExe.Source config remote.dagshub.password
        }
    }
}
if ($env:PHASE5_DVC_URL -eq "$project.dvc") {
    if (-not $env:MLFLOW_TRACKING_URI) { $env:MLFLOW_TRACKING_URI = "$project.mlflow" }
    if ($env:MLFLOW_TRACKING_URI -eq "$project.mlflow" -and
        -not $env:MLFLOW_TRACKING_USERNAME -and -not $env:MLFLOW_TRACKING_PASSWORD) {
        $env:MLFLOW_TRACKING_USERNAME = $env:PHASE5_DVC_USER
        $env:MLFLOW_TRACKING_PASSWORD = $env:PHASE5_DVC_PASSWORD
    }
}
# Read configured credentials. Secret values stay out of command arguments.
$keys = @('MLFLOW_TRACKING_URI', 'MLFLOW_TRACKING_USERNAME', 'MLFLOW_TRACKING_PASSWORD',
          'PHASE5_DVC_URL', 'PHASE5_DVC_USER', 'PHASE5_DVC_PASSWORD')
$data = @{}
foreach ($key in $keys) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if ([string]::IsNullOrWhiteSpace($value)) { throw "Set $key first. See docs/kubeflow-setup.md." }
    $data[$key] = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($value))
}
docker network inspect minikube *> $null
Check 'Minikube Docker network lookup'
minikube -p minikube image load $runtime --daemon
Check 'Minikube image load'
$secret = @{apiVersion='v1'; kind='Secret'; type='Opaque'; metadata=@{name='credit-risk-phase5';namespace='kubeflow'}; data=$data}
($secret | ConvertTo-Json -Compress -Depth 5) | kubectl @k apply --server-side --field-manager=phase5-setup -f -
Check 'KFP runtime secret'
kubectl @k apply -f pipelines/airflow/kfp-access.yaml
Check 'Airflow access service'
docker compose -f pipelines/airflow/compose.yaml up -d
Check 'Airflow startup'
Write-Output 'Airflow UI: http://localhost:8085. From the repository root, open docs/orchestration.md for the first run.'
