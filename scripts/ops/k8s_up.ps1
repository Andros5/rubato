# Local test cluster with kind: build, load images, apply manifests and wait.
# Requires kind (https://kind.sigs.k8s.io) and Docker. Run from the repository root.
$ErrorActionPreference = 'Stop'
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) { throw 'kind is not installed' }
if (-not ((kind get clusters) -contains 'rubato')) {
    kind create cluster --name rubato --config deploy/kind/kind-config.yaml
}
docker build -t rubato:1.0.0 .
docker pull postgres:17-alpine
kind load docker-image rubato:1.0.0 postgres:17-alpine --name rubato
if (-not (Test-Path deploy/k8s/secrets.env)) {
    function New-Token { -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) }
    $pg = New-Token
    @(
        "POSTGRES_PASSWORD=$pg",
        "RUBATO_DSN=postgresql://rubato:$pg@postgres:5432/rubato",
        "RUBATO_CLIENTS=admin:$(New-Token):admin:0,radar:$(New-Token):client:1,citalab:$(New-Token):client:1"
    ) | Set-Content -Encoding ascii deploy/k8s/secrets.env
}
kubectl apply -k deploy/k8s
kubectl -n rubato rollout status statefulset/postgres --timeout=180s
kubectl -n rubato rollout status deployment/api --timeout=180s
kubectl -n rubato rollout status deployment/scheduler --timeout=180s
kubectl -n rubato rollout status deployment/provider --timeout=180s
kubectl -n rubato get pods -o wide
