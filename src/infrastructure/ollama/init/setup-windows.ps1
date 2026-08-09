param(
    [string]$ChatModel = "gemma4:e2b",
    [string]$ToolModel = "qwen3.5:4b",
    [string]$FallbackToolModel = "qwen2.5:latest",
    [string]$EmbeddingModel = "ibm/granite-embedding:30m",
    [string]$HostBinding = "0.0.0.0:11434",
    [string]$KeepAlive = "5m",
    [ValidateRange(1, 16)]
    [int]$NumParallel = 1
)

$ErrorActionPreference = "Stop"

$ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
if (-not $ollama) {
    throw "Ollama for Windows is not installed or ollama.exe is not available in PATH. Install Ollama for Windows, start it once, then rerun this script."
}

Write-Host "Configuring Ollama for native Windows execution..."
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", $HostBinding, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", $KeepAlive, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", $NumParallel.ToString(), "User")

Write-Host "Persistent user variables configured:"
Write-Host "  OLLAMA_HOST=$HostBinding"
Write-Host "  OLLAMA_KEEP_ALIVE=$KeepAlive"
Write-Host "  OLLAMA_NUM_PARALLEL=$NumParallel"

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 5 | Out-Null
}
catch {
    throw "Ollama is installed but the local API is not running on 127.0.0.1:11434. Start Ollama from the Windows Start menu and rerun this script."
}

$models = @($ChatModel, $ToolModel, $FallbackToolModel, $EmbeddingModel) | Select-Object -Unique
foreach ($model in $models) {
    Write-Host "Ensuring model is available: $model"
    & ollama pull $model
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to pull Ollama model '$model'."
    }
}

Write-Host "Ollama models are ready on the Windows host."
Write-Host "IMPORTANT: quit Ollama from the Windows tray and start it again so it inherits the new OLLAMA_HOST setting."
Write-Host "After restart, verify from PowerShell with: curl.exe http://localhost:11434/api/tags"
Write-Host "Then verify Docker access with: docker run --rm curlimages/curl:8.10.1 -fsS http://host.docker.internal:11434/api/tags"
