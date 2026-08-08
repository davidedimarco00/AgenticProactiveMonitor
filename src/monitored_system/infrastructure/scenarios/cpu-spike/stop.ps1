$ErrorActionPreference = "Stop"

$container = "processing-service"
$remoteScript = "/tmp/monitored-cpu-spike.py"
$pidFile = "/var/run/monitored-faults/cpu-spike.pid"

Write-Host "Stopping scenario: cpu-spike"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running."
}

docker exec $container sh -c "if [ -s $pidFile ]; then kill \$(cat $pidFile) 2>/dev/null || true; fi; rm -f $pidFile $remoteScript /tmp/monitored-cpu-spike.log"

Write-Host "Scenario stopped. CPU load should return to the normal baseline."
