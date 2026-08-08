param(
    [int]$DelayMs = 2000
)

$ErrorActionPreference = "Stop"

if ($DelayMs -lt 1 -or $DelayMs -gt 30000) {
    throw "DelayMs must be between 1 and 30000."
}

$container = "processing-service"
$faultFile = "/var/run/monitored-faults/processing-delay-ms"

Write-Host "Starting scenario: high-latency"
Write-Host "Applying $DelayMs ms delay to '$container'..."

docker exec $container sh -c "mkdir -p /var/run/monitored-faults && echo $DelayMs > $faultFile"

Write-Host "Scenario active. Controlled processing delay: $DelayMs ms."
Write-Host "Run .\infrastructure\scenarios\high-latency\stop.ps1 to remove it."
