# Local test cluster with kind: build, load images, apply manifests and wait.
# Requires kind (https://kind.sigs.k8s.io) and a running Docker engine. Run from the repository root.
$ErrorActionPreference = 'Stop'
function Invoke-Step { param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) { throw 'kind is not installed' }
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'the Docker engine is not running' }
if (-not ((kind get clusters) -contains 'rubato')) {
    Invoke-Step kind @('create', 'cluster', '--name', 'rubato', '--config', 'deploy/kind/kind-config.yaml')
}
Invoke-Step kubectl @('config', 'use-context', 'kind-rubato')
Invoke-Step docker @('build', '-q', '-t', 'rubato:1.1.0', '.')
# 'kind load docker-image' fails with Docker's containerd image store (multi-platform digests);
# a single-platform archive works. postgres is pulled by the node itself.
$archive = Join-Path $env:TEMP 'rubato-1.1.0.tar'
Invoke-Step docker @('save', '--platform', 'linux/amd64', '-o', $archive, 'rubato:1.1.0')
Invoke-Step kind @('load', 'image-archive', $archive, '--name', 'rubato')
Remove-Item $archive
if (-not (Test-Path deploy/k8s/secrets.env)) {
    function New-Token { -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) }
    $pg = New-Token
    @(
        "POSTGRES_PASSWORD=$pg",
        "RUBATO_DSN=postgresql://rubato:$pg@postgres:5432/rubato",
        "RUBATO_CLIENTS=admin:$(New-Token):admin:0,radar:$(New-Token):client:1,citalab:$(New-Token):client:1"
    ) | Set-Content -Encoding ascii deploy/k8s/secrets.env
}
Invoke-Step kubectl @('apply', '-k', 'deploy/k8s')
Invoke-Step kubectl @('-n', 'rubato', 'rollout', 'status', 'statefulset/postgres', '--timeout=300s')
Invoke-Step kubectl @('-n', 'rubato', 'rollout', 'status', 'deployment/api', '--timeout=300s')
Invoke-Step kubectl @('-n', 'rubato', 'rollout', 'status', 'deployment/scheduler', '--timeout=300s')
Invoke-Step kubectl @('-n', 'rubato', 'rollout', 'status', 'deployment/provider', '--timeout=300s')
Invoke-Step kubectl @('-n', 'rubato', 'get', 'pods', '-o', 'wide')
