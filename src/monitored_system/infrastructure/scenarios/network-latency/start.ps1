param(
    [ValidateRange(1, 2000)]
    [int]$DelayMs = 250,

    [ValidateRange(0, 1000)]
    [int]$JitterMs = 0
)

$ErrorActionPreference = "Stop"

$container = "monitored-toxiproxy"
$proxy = "api-gateway-processing-service"
$toxic = "network-latency"

Write-Host "Starting scenario: network-latency"
Write-Host "Source: api-gateway"
Write-Host "Destination: processing-service"
Write-Host "Injected downstream latency: $DelayMs ms"
if ($JitterMs -gt 0) {
    Write-Host "Injected jitter: +/- $JitterMs ms"
}

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running. Recreate the monitored system after pulling the latest branch."
}

$existing = docker exec $container /toxiproxy-cli inspect $proxy 2>$null | Select-String $toxic
if ($existing) {
    throw "Network latency scenario is already active. Run stop.ps1 first."
}

$args = @(
    "exec", $container,
    "/toxiproxy-cli", "toxic", "add",
    "-n", $toxic,
    "-t", "latency",
    "-a", "latency=$DelayMs",
    "-a", "jitter=$JitterMs",
    $proxy
)

& docker @args
if ($LASTEXITCODE -ne 0) {
    throw "Unable to create the Toxiproxy latency toxic."
}

Write-Host "Scenario active. api-gateway requests to processing-service now traverse the injected latency."
Write-Host "Telegraf network_service_latency.response_time should increase while ICMP ping remains available as an independent reference."
Write-Host "Run .\infrastructure\scenarios\network-latency\stop.ps1 to restore the normal path."
