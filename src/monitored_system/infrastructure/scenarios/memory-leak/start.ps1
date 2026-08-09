param(
    [ValidateRange(64, 2048)]
    [int]$TotalMB = 512,

    [ValidateRange(1, 512)]
    [int]$StepMB = 32,

    [ValidateRange(1, 60)]
    [int]$StepSeconds = 2
)

$ErrorActionPreference = "Stop"

if ($StepMB -gt $TotalMB) {
    throw "StepMB cannot be greater than TotalMB."
}

$container = "worker-service"
$remoteScript = "/tmp/monitored-memory-leak.py"
$pidFile = "/var/run/monitored-faults/memory-leak.pid"

Write-Host "Starting scenario: memory-leak"
Write-Host "Target: $container"
Write-Host "Growth: $StepMB MB every $StepSeconds second(s), up to $TotalMB MB"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running."
}

docker exec $container sh -c "test -s $pidFile" 2>$null
if ($LASTEXITCODE -eq 0) {
    throw "Memory leak scenario is already active. Run stop.ps1 first."
}

docker exec $container sh -c "mkdir -p /var/run/monitored-faults"
docker cp "$PSScriptRoot\memory_leak.py" "${container}:$remoteScript" | Out-Null

$command = "python3 $remoteScript --total-mb $TotalMB --step-mb $StepMB --step-seconds $StepSeconds >/tmp/monitored-memory-leak.log 2>&1 & echo `$! > $pidFile"
docker exec $container sh -c $command
if ($LASTEXITCODE -ne 0) {
    throw "Unable to start the memory leak workload in '$container'."
}

Start-Sleep -Seconds 1
$controllerPid = (docker exec $container sh -c "cat $pidFile" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($controllerPid)) {
    throw "Memory leak started, but the controller PID could not be read from '$pidFile'."
}

Write-Host "Scenario active. Controller PID: $controllerPid"
Write-Host "Run .\infrastructure\scenarios\memory-leak\stop.ps1 to release the injected memory."
