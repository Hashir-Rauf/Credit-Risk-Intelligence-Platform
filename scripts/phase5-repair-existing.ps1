# For the existing local dev installation that attempted the incompatible SQL migration.
$ErrorActionPreference = 'Stop'
$k = @('--context', 'minikube', '-n', 'kubeflow')
function Check([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed." }
}
$raw = kubectl @k get deployment/ml-pipeline -o json
Check 'API lookup'
$api = ($raw | ConvertFrom-Json).spec.template.spec.containers |
    Where-Object { $_.name -eq 'ml-pipeline-api-server' }
if ($api.image -notin @('ghcr.io/kubeflow/kfp-api-server:master','ghcr.io/kubeflow/kfp-api-server:2.18.2')) {
    throw "Unexpected API image $($api.image). Check this cluster before applying this recovery."
}
$db = $api.env | Where-Object { $_.name -eq 'DBCONFIG_MYSQLCONFIG_DBNAME' }
if (($db.value -notin @('mlpipeline','mlpipeline_phase5_2182')) -and ($db.valueFrom.configMapKeyRef.key -ne 'pipelineDb')) {
    throw 'The deployment uses a custom database. Inspect it before selecting another database.'
}
# Change the API image, DB and workflow driver/launcher together, so master never initializes the new DB.
$patch = @{spec=@{template=@{spec=@{containers=@(@{
    name='ml-pipeline-api-server'; image='ghcr.io/kubeflow/kfp-api-server:2.18.2'; env=@(
        @{name='DBCONFIG_MYSQLCONFIG_DBNAME';value='mlpipeline_phase5_2182';valueFrom=$null},
        @{name='V2_DRIVER_IMAGE';value='ghcr.io/kubeflow/kfp-driver:2.18.2'},
        @{name='V2_LAUNCHER_IMAGE';value='ghcr.io/kubeflow/kfp-launcher:2.18.2'}
    )
})}}}}
$file = New-TemporaryFile
try {
    [IO.File]::WriteAllText($file.FullName, ($patch | ConvertTo-Json -Depth 10))
    kubectl @k patch deployment/ml-pipeline --type=strategic --patch-file $file.FullName
    Check 'API release and schema selection'
} finally { Remove-Item -LiteralPath $file.FullName }

$raw = kubectl @k get deployments -o json
Check 'Controller lookup'
foreach ($deployment in ($raw | ConvertFrom-Json).items) {
    if ($deployment.metadata.name -eq 'proxy-agent') { continue }
    foreach ($container in $deployment.spec.template.spec.containers) {
        if ($container.image -match '^(ghcr\.io/kubeflow/kfp-[a-z0-9-]+):master$') {
            $image = "$($Matches[1]):2.18.2"
            kubectl @k set image "deployment/$($deployment.metadata.name)" "$($container.name)=$image"
            Check "Pinning $($container.name)"
        }
    }
}
foreach ($name in @('ml-pipeline','ml-pipeline-ui','ml-pipeline-persistenceagent','ml-pipeline-scheduledworkflow')) {
    kubectl @k rollout status "deployment/$name" --timeout=10m
    Check "$name rollout"
}
