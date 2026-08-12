param(
    [string]$McpUrl = "http://127.0.0.1:8000/mcp",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$Model = "gemma4:e2b",
    [int]$Limit = 5
)

$ErrorActionPreference = "Stop"

$Question = "What exact SQLite path does data-service use, which Docker volume backs it, and what is the exact OpenSearch detector name for the processing-service to data-service link?"

function Assert-Contains {
    param(
        [string]$Text,
        [string]$Expected,
        [string]$Message
    )

    if (-not $Text.Contains($Expected)) {
        throw "ASSERTION FAILED: $Message`nExpected to find: $Expected`nActual answer:`n$Text"
    }
}

Write-Host "[1/3] Calling search_knowledge through MCP..."

$inspectorArgs = @(
    "@modelcontextprotocol/inspector",
    "--cli",
    $McpUrl,
    "--transport",
    "http",
    "--method",
    "tools/call",
    "--tool-name",
    "search_knowledge",
    "--tool-arg",
    "query=$Question",
    "--tool-arg",
    "limit=$Limit"
)

$rawMcp = (& npx @inspectorArgs 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "MCP Inspector failed:`n$rawMcp"
}

$outer = $rawMcp | ConvertFrom-Json
$textContent = $outer.content | Where-Object { $_.type -eq "text" } | Select-Object -First 1
if (-not $textContent) {
    throw "MCP response did not contain text content."
}

$retrieval = $textContent.text | ConvertFrom-Json
if ($retrieval.status -ne "ok") {
    throw "search_knowledge returned status '$($retrieval.status)'."
}
if ($retrieval.returned_results -lt 1) {
    throw "search_knowledge returned no chunks."
}

Write-Host "Retrieved $($retrieval.returned_results) chunks from $($retrieval.collection)."

$contextBlocks = foreach ($result in $retrieval.results) {
    "SOURCE: $($result.source_path) | score=$($result.score)`n$($result.text)"
}
$Context = $contextBlocks -join "`n`n"

Write-Host "[2/3] Asking Gemma to answer only from retrieved context..."

$Prompt = @"
You are answering a question about a specific monitored software system.
Use ONLY the retrieved context below. Do not use prior assumptions and do not invent missing values.

Question:
$Question

Retrieved context:
$Context

Return exactly these three facts, one per line:
SQLite path: <value>
Docker volume: <value>
Detector: <value>
"@

$ollamaBody = @{
    model = $Model
    prompt = $Prompt
    stream = $false
    options = @{
        temperature = 0
    }
} | ConvertTo-Json -Depth 10

$ollamaResponse = Invoke-RestMethod `
    -Method Post `
    -Uri "$OllamaUrl/api/generate" `
    -ContentType "application/json" `
    -Body $ollamaBody

$answer = [string]$ollamaResponse.response
Write-Host ""
Write-Host "Gemma grounded answer:"
Write-Host $answer
Write-Host ""

Write-Host "[3/3] Verifying exact project-specific facts..."
Assert-Contains $answer "/var/lib/notes/notes.db" "Gemma did not recover the exact SQLite path from RAG."
Assert-Contains $answer "notes-data" "Gemma did not recover the exact Docker volume from RAG."
Assert-Contains $answer "NETLAT-processing-service-data-service" "Gemma did not recover the exact detector name from RAG."

Write-Host "Grounded RAG answer test PASSED." -ForegroundColor Green
