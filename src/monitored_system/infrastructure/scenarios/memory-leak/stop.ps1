$ErrorActionPreference = "Stop"

$container = "worker-service"
$remoteScript = "/tmp/monitored-memory-leak.py"
$pidFile = "/var/run/monitored-faults/memory-leak.pid"

Write-Host "Stopping scenario: memory-leak"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running."
}

docker exec $container sh -c "if [ -s $pidFile ]; then xargs kill < $pidFile 2>/dev/null || true; fi; rm -f $pidFile $remoteScript /tmp/monitored-memory-leak.log"

Write-Host "Scenario stopped. Injected memory has been released."
