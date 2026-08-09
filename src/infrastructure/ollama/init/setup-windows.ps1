param(
    [string]$ChatModel = "gemma4:e2b",
    [string]$ToolModel = "qwen3.5:4b",
    [string]$FallbackToolModel = "qwen2.5:latest",
    [string]$EmbeddingModel = "ibm/granite-embedding:30m",
    [string]$HostBinding = "0.0.0.0:11434",
    [string]$KeepAlive = "5m",
    [ValidateRange(1, 16)]
    [int]$NumParallel = 1,
    [switch]$PullModels
)

$ErrorActionPreference = "Stop"

$ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
if (-not $ollama) {
    throw "Ollama for Windows is not installed or ollama.exe is not available in PATH. Install Ollama for Windows, start it once, then rerun this script."
}

$modelDirectory = Join-Path $env:USERPROFILE ".ollama\models"
New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null

Write-Host "Configuring Ollama for native Windows execution..."
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", $HostBinding, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", $KeepAlive, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", $NumParallel.ToString(), "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $modelDirectory, "User")

Write-Host "Persistent user variables configured:"
Write-Host "  OLLAMA_HOST=$HostBinding"
Write-Host "  OLLAMA_KEEP_ALIVE=$KeepAlive"
Write-Host "  OLLAMA_NUM_PARALLEL=$NumParallel"
Write-Host "  OLLAMA_MODELS=$modelDirectory"

if (-not $PullModels) {
    Write-Host ""
    Write-Host "Configuration saved. Quit Ollama completely from the Windows tray and start it again from the Start menu."
    Write-Host "Then rerun this script with -PullModels so the restarted Ollama server uses the new environment."
    Write-Host "Example: .\setup-windows.ps1 -PullModels"
    exit 0
}

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 5 | Out-Null
}
catch {
    throw "The Ollama API is not running on 127.0.0.1:11434. Start Ollama from the Windows Start menu after applying the configuration, then rerun this script with -PullModels."
}

$models = @($ChatModel, $ToolModel, $FallbackToolModel, $EmbeddingModel) | Select-Object -Unique
foreach ($model in $models) {
    Write-Host "Ensuring model is available: $model"
    & ollama pull $model
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to pull Ollama model '$model'. Check $env:LOCALAPPDATA\Ollama\server.log and the model directory '$modelDirectory'."
    }
}

Write-Host "Ollama models are ready on the Windows host."
Write-Host "Verify locally with: curl.exe http://localhost:11434/api/tags"
Write-Host "Verify Docker access with: docker run --rm curlimages/curl:8.10.1 -fsS http://host.docker.internal:11434/api/tags"
