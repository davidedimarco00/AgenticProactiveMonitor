$ErrorActionPreference = "Stop"

$container = "api-gateway"
$stateFile = "/var/run/monitored-faults/network-latency.interface"

Write-Host "Stopping scenario: network-latency"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running."
}

$networkInterface = docker exec $container sh -c "cat $stateFile 2>/dev/null || true"
$networkInterface = ($networkInterface | Select-Object -First 1).Trim()

if (-not $networkInterface) {
    Write-Host "No active network-latency state was found. Nothing to remove."
    exit 0
}

docker exec $container tc qdisc del dev $networkInterface root 2>$null | Out-Null
# tc returns a non-zero code when no qdisc exists; cleanup should still be idempotent.
$global:LASTEXITCODE = 0

docker exec $container sh -c "rm -f $stateFile"

Write-Host "Scenario stopped. Removed netem from interface '$networkInterface'."
Write-Host "Network latency should return to the normal baseline."
