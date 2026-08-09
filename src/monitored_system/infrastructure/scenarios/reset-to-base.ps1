$ErrorActionPreference = "Stop"

$monitoredRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Write-Host "Restoring monitored system to base scenario..."
Push-Location $monitoredRoot
try {
    docker compose up -d
}
finally {
    Pop-Location
}

$processing = "processing-service"
$worker = "worker-service"
$data = "data-service"
$gateway = "api-gateway"

# Remove controlled processing latency.
docker exec $processing sh -c "rm -f /var/run/monitored-faults/processing-delay-ms" 2>$null

# Stop only the injected CPU workload, if present.
docker exec $processing sh -c "if [ -s /var/run/monitored-faults/cpu-spike.pid ]; then xargs kill < /var/run/monitored-faults/cpu-spike.pid 2>/dev/null || true; fi; rm -f /var/run/monitored-faults/cpu-spike.pid /tmp/monitored-cpu-spike.py /tmp/monitored-cpu-spike.log" 2>$null

# Stop only the injected memory workload, if present.
docker exec $worker sh -c "if [ -s /var/run/monitored-faults/memory-leak.pid ]; then xargs kill < /var/run/monitored-faults/memory-leak.pid 2>/dev/null || true; fi; rm -f /var/run/monitored-faults/memory-leak.pid /tmp/monitored-memory-leak.py /tmp/monitored-memory-leak.log" 2>$null

# Remove tc/netem from the application-network interface, if the network fault
# recorded one. This does not touch the separate observability interface.
$networkInterface = docker exec $gateway sh -c "cat /var/run/monitored-faults/network-latency.interface 2>/dev/null || true"
$networkInterface = ($networkInterface | Select-Object -First 1).Trim()
if ($networkInterface) {
    docker exec $gateway tc qdisc del dev $networkInterface root 2>$null | Out-Null
    docker exec $gateway sh -c "rm -f /var/run/monitored-faults/network-latency.interface"
}

# Ensure the persistence service is running again.
docker start $data 2>$null | Out-Null

Write-Host "Base scenario restored: normal traffic enabled and controlled faults removed."
Write-Host "The traffic-generator remains active as the normal workload."
