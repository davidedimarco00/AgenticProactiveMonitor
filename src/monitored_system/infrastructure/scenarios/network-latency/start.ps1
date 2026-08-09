param(
    [ValidateRange(1, 2000)]
    [int]$DelayMs = 250,

    [ValidateRange(0, 1000)]
    [int]$JitterMs = 0
)

$ErrorActionPreference = "Stop"

$container = "api-gateway"
$destination = "processing-service"
$stateFile = "/var/run/monitored-faults/network-latency.interface"

Write-Host "Starting scenario: network-latency"
Write-Host "Source: $container"
Write-Host "Destination: $destination"
Write-Host "Injected egress delay: $DelayMs ms"
if ($JitterMs -gt 0) {
    Write-Host "Injected jitter: +/- $JitterMs ms"
}

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running."
}

$existingInterface = docker exec $container sh -c "cat $stateFile 2>/dev/null || true"
if ($existingInterface) {
    throw "Network latency scenario is already active on interface '$existingInterface'. Run stop.ps1 first."
}

$destinationLine = docker exec $container getent ahostsv4 $destination | Select-Object -First 1
if (-not $destinationLine) {
    throw "Unable to resolve destination '$destination' from '$container'."
}

$destinationIp = ($destinationLine -split '\s+')[0]
$route = docker exec $container ip route get $destinationIp
$match = [regex]::Match(($route -join ' '), '\bdev\s+(\S+)')
if (-not $match.Success) {
    throw "Unable to determine the application interface used to reach $destination ($destinationIp)."
}

$networkInterface = $match.Groups[1].Value
Write-Host "Application route: $destinationIp via $networkInterface"

if ($JitterMs -gt 0) {
    docker exec $container tc qdisc replace dev $networkInterface root netem delay "${DelayMs}ms" "${JitterMs}ms"
}
else {
    docker exec $container tc qdisc replace dev $networkInterface root netem delay "${DelayMs}ms"
}

if ($LASTEXITCODE -ne 0) {
    throw "Unable to apply tc/netem. Verify that api-gateway has NET_ADMIN capability."
}

docker exec $container sh -c "mkdir -p /var/run/monitored-faults && echo $networkInterface > $stateFile"

Write-Host "Scenario active. Traffic from api-gateway to processing-service is now delayed."
Write-Host "Observability traffic uses the separate observability network and is not the selected route."
Write-Host "Run .\infrastructure\scenarios\network-latency\stop.ps1 to restore the normal network path."
