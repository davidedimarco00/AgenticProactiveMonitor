$ErrorActionPreference = "Stop"

$container = "processing-service"
$faultFile = "/var/run/monitored-faults/processing-delay-ms"

Write-Host "Stopping scenario: high-latency"

docker exec $container sh -c "rm -f $faultFile"

Write-Host "High-latency fault removed. Processing service returned to normal mode."
