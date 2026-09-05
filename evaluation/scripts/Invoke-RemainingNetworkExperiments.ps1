param(
    [int]$Repetitions = 5,
    [int]$RecoverySeconds = -1
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$runner = Join-Path $PSScriptRoot "Invoke-ConfigurationExperiment.ps1"
if (-not (Test-Path $runner)) {
    throw "Configuration experiment runner not found: $runner"
}

$experiments = @(
    "more-reasoning",
    "different-model",
    "higher-temperature"
)

Write-Host "" -ForegroundColor Cyan
Write-Host "=== Remaining Network Evaluation Campaigns ===" -ForegroundColor Cyan
Write-Host "Scenario: network-latency"
Write-Host "Repetitions per experiment: $Repetitions"
Write-Host "Experiments: $($experiments -join ', ')"
Write-Host "The script will stop immediately if one experiment fails." -ForegroundColor Yellow

foreach ($experiment in $experiments) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "Starting experiment: $experiment" -ForegroundColor Cyan
    Write-Host "Started at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "============================================================" -ForegroundColor Cyan

    $arguments = @{
        Experiment = $experiment
        Scenario = "network-latency"
        Repetitions = $Repetitions
        PrepareEnvironment = $true
    }

    if ($RecoverySeconds -ge 0) {
        $arguments["RecoverySeconds"] = $RecoverySeconds
    }

    try {
        & $runner @arguments
    }
    catch {
        Write-Host "" -ForegroundColor Red
        Write-Host "Experiment '$experiment' failed. Remaining experiments were NOT started." -ForegroundColor Red
        Write-Host "Failure time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Red
        throw
    }

    Write-Host ""
    Write-Host "Completed experiment: $experiment" -ForegroundColor Green
    Write-Host "Completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "All remaining Network experiments completed successfully." -ForegroundColor Green
Write-Host "Completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
Write-Host "Check evaluation/results for the three generated result directories." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
