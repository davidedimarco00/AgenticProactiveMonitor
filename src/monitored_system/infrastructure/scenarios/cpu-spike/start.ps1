param(
    [ValidateRange(1, 8)]
    [int]$Workers = 4
)

$ErrorActionPreference = "Stop"

$container = "processing-service"
$remoteScript = "/tmp/monitored-cpu-spike.py"
$pidFile = "/var/run/monitored-faults/cpu-spike.pid"

Write-Host "Starting scenario: cpu-spike"
Write-Host "Target: $container"
Write-Host "CPU workers: $Workers"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running."
}

docker exec $container sh -c "test -s $pidFile" 2>$null
if ($LASTEXITCODE -eq 0) {
    throw "CPU spike scenario is already active. Run stop.ps1 first."
}

docker exec $container sh -c "mkdir -p /var/run/monitored-faults"
docker cp "$PSScriptRoot\cpu_spike.py" "${container}:$remoteScript" | Out-Null

$command = "python3 $remoteScript --workers $Workers >/tmp/monitored-cpu-spike.log 2>&1 & echo `$! > $pidFile"
docker exec $container sh -c $command
if ($LASTEXITCODE -ne 0) {
    throw "Unable to start the CPU spike workload in '$container'."
}

Start-Sleep -Seconds 1
$controllerPid = (docker exec $container sh -c "cat $pidFile" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($controllerPid)) {
    throw "CPU spike started, but the controller PID could not be read from '$pidFile'."
}

Write-Host "Scenario active. Controller PID: $controllerPid"
Write-Host "Run .\infrastructure\scenarios\cpu-spike\stop.ps1 to restore normal CPU load."
