param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("more-reasoning", "different-model", "higher-temperature")]
    [string]$Experiment,
    [string]$Scenario = "all",
    [int]$Repetitions = 0,
    [int]$RecoverySeconds = -1,
    [switch]$PrepareEnvironment,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$evaluationRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$profilesPath = Join-Path $evaluationRoot "config\model-profiles.json"
$invokeScript = Join-Path $PSScriptRoot "Invoke-Evaluation.ps1"

$profilesDocument = Get-Content -Raw -Path $profilesPath | ConvertFrom-Json
$profileProperty = $profilesDocument.profiles.PSObject.Properties[$Experiment]
if ($null -eq $profileProperty) {
    throw "Evaluation profile '$Experiment' was not found in $profilesPath"
}
$profile = $profileProperty.Value

$maxSteps = [int]$profile.max_react_steps
$temperature = [double]$profile.temperature
if ($maxSteps -le 0) {
    throw "Profile max_react_steps must be greater than zero."
}
if ($temperature -lt 0.0 -or $temperature -gt 2.0) {
    throw "Profile temperature must be between 0 and 2."
}

$previousSteps = [Environment]::GetEnvironmentVariable(
    "AGENT_REACT_MAX_STEPS",
    [EnvironmentVariableTarget]::Process
)
$previousTemperature = [Environment]::GetEnvironmentVariable(
    "AGENT_REASONING_TEMPERATURE",
    [EnvironmentVariableTarget]::Process
)

try {
    $env:AGENT_REACT_MAX_STEPS = [string]$maxSteps
    $env:AGENT_REASONING_TEMPERATURE = $temperature.ToString(
        [System.Globalization.CultureInfo]::InvariantCulture
    )

    Write-Host ""
    Write-Host "=== Configuration experiment: $Experiment ===" -ForegroundColor Cyan
    Write-Host "Reasoning model: $($profile.reasoning_model)"
    Write-Host "Tool model: $($profile.tool_model)"
    Write-Host "Embedding model: $($profile.embedding_model)"
    Write-Host "Max ReAct steps: $maxSteps"
    Write-Host "Specialist reasoning/finalization temperature: $($env:AGENT_REASONING_TEMPERATURE)"
    Write-Host "Scenario selection: $Scenario"

    $arguments = @{
        Campaign = $Experiment
        Scenario = $Scenario
    }
    if ($Repetitions -gt 0) {
        $arguments["Repetitions"] = $Repetitions
    }
    if ($RecoverySeconds -ge 0) {
        $arguments["RecoverySeconds"] = $RecoverySeconds
    }
    if ($PrepareEnvironment) {
        $arguments["PrepareEnvironment"] = $true
    }
    if ($PreflightOnly) {
        $arguments["PreflightOnly"] = $true
    }

    & $invokeScript @arguments
}
finally {
    if ($null -eq $previousSteps) {
        Remove-Item Env:AGENT_REACT_MAX_STEPS -ErrorAction SilentlyContinue
    }
    else {
        $env:AGENT_REACT_MAX_STEPS = $previousSteps
    }

    if ($null -eq $previousTemperature) {
        Remove-Item Env:AGENT_REASONING_TEMPERATURE -ErrorAction SilentlyContinue
    }
    else {
        $env:AGENT_REASONING_TEMPERATURE = $previousTemperature
    }
}
