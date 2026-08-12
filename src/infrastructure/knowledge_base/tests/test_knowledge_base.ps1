param(
    [string]$QdrantUrl = "http://127.0.0.1:6333",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$EmbeddingModel = "ibm/granite-embedding:30m"
)

$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Get-CollectionInfo {
    param([string]$Collection)
    return (Invoke-RestMethod -Method Get -Uri "$QdrantUrl/collections/$Collection").result
}

function Search-Collection {
    param(
        [string]$Collection,
        [string]$Query
    )

    $embedBody = @{
        model = $EmbeddingModel
        input = @($Query)
    } | ConvertTo-Json -Depth 8

    $embeddingResponse = Invoke-RestMethod `
        -Method Post `
        -Uri "$OllamaUrl/api/embed" `
        -ContentType "application/json" `
        -Body $embedBody

    $vector = $embeddingResponse.embeddings[0]
    Assert-True ($vector.Count -eq 384) "Embedding vector must contain 384 dimensions."

    $queryBody = @{
        query = $vector
        limit = 3
        with_payload = $true
        with_vector = $false
    } | ConvertTo-Json -Depth 12

    return Invoke-RestMethod `
        -Method Post `
        -Uri "$QdrantUrl/collections/$Collection/points/query" `
        -ContentType "application/json" `
        -Body $queryBody
}

Write-Host "[1/5] Checking Qdrant collections..."
$requiredCollections = @(
    "monitored-system",
    "kb-system-engineer-linux",
    "kb-network-engineer",
    "kb-application-engineer",
    "kb-software-developer",
    "kb-technical-lead"
)

$collectionResponse = Invoke-RestMethod -Method Get -Uri "$QdrantUrl/collections"
$existingCollections = @($collectionResponse.result.collections | ForEach-Object { $_.name })

foreach ($collection in $requiredCollections) {
    Assert-True ($existingCollections -contains $collection) "Missing Qdrant collection: $collection"
}

Write-Host "[2/5] Checking ingested collection counts..."
$monitoredInfo = Get-CollectionInfo "monitored-system"
$linuxInfo = Get-CollectionInfo "kb-system-engineer-linux"

Assert-True ($monitoredInfo.points_count -gt 0) "monitored-system must contain ingested chunks."
Assert-True ($linuxInfo.points_count -gt 0) "kb-system-engineer-linux must contain ingested chunks."

Write-Host "monitored-system points: $($monitoredInfo.points_count)"
Write-Host "kb-system-engineer-linux points: $($linuxInfo.points_count)"

Write-Host "[3/5] Testing monitored-system semantic retrieval..."
$sharedSearch = Search-Collection `
    -Collection "monitored-system" `
    -Query "processing-service dependency on data-service and request flow"

Assert-True ($sharedSearch.result.points.Count -gt 0) "Shared collection search returned no results."
$sharedPayload = $sharedSearch.result.points[0].payload
Assert-True ($sharedPayload.collection -eq "monitored-system") "Shared result came from the wrong collection."
Assert-True ($sharedPayload.managed_by -eq "manifest-ingest") "Shared result was not produced by manifest ingestion."
Assert-True (-not ($sharedPayload.PSObject.Properties.Name -contains "incident_types")) "incident_types must not be persisted as a Qdrant diagnosis label."

Write-Host "[4/5] Testing System Engineer Linux retrieval..."
$linuxSearch = Search-Collection `
    -Collection "kb-system-engineer-linux" `
    -Query "Linux CPU load process accounting procfs cgroup memory"

Assert-True ($linuxSearch.result.points.Count -gt 0) "Linux role collection search returned no results."
$linuxPayload = $linuxSearch.result.points[0].payload
Assert-True ($linuxPayload.collection -eq "kb-system-engineer-linux") "Linux result came from the wrong collection."
Assert-True ($linuxPayload.roles -contains "system_engineer") "Linux result is missing system_engineer role metadata."
Assert-True ($linuxPayload.managed_by -eq "manifest-ingest") "Linux result was not produced by manifest ingestion."
Assert-True (-not ($linuxPayload.PSObject.Properties.Name -contains "incident_types")) "Linux payload must not contain diagnosis labels."

Write-Host "[5/5] Checking empty specialist scaffolds..."
foreach ($collection in @(
    "kb-network-engineer",
    "kb-application-engineer",
    "kb-software-developer",
    "kb-technical-lead"
)) {
    $info = Get-CollectionInfo $collection
    Write-Host "$collection points: $($info.points_count)"
}

Write-Host "Knowledge-base smoke test PASSED." -ForegroundColor Green
