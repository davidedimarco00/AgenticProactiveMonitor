$ErrorActionPreference = "Stop"

$container = "monitored-toxiproxy"
$proxy = "api-gateway-processing-service"
$toxic = "latency_downstream"

Write-Host "Stopping scenario: network-latency"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    Write-Host "Toxiproxy is not running. Nothing to remove."
    exit 0
}

docker exec $container /toxiproxy-cli toxic remove -n $toxic $proxy 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "No active network-latency toxic was found."
    $global:LASTEXITCODE = 0
    exit 0
}

Write-Host "Scenario stopped. Toxiproxy latency removed."
Write-Host "Network service latency should return to the normal baseline."
