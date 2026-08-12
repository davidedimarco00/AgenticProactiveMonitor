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

Write-Host "[1/4] Checking Qdrant collection..."
$collectionResponse = Invoke-RestMethod -Method Get -Uri "$QdrantUrl/collections"
$existingCollections = @($collectionResponse.result.collections | ForEach-Object { $_.name })
Assert-True ($existingCollections -contains "monitored-system") "Missing Qdrant collection: monitored-system"

Write-Host "[2/4] Checking ingested collection count..."
$monitoredInfo = Get-CollectionInfo "monitored-system"
Assert-True ($monitoredInfo.points_count -gt 0) "monitored-system must contain ingested chunks."
Write-Host "monitored-system points: $($monitoredInfo.points_count)"

Write-Host "[3/4] Testing monitored-system semantic retrieval..."
$search = Search-Collection `
    -Collection "monitored-system" `
    -Query "processing-service dependency on data-service and request flow"

Assert-True ($search.result.points.Count -gt 0) "monitored-system search returned no results."
$payload = $search.result.points[0].payload
Assert-True ($payload.collection -eq "monitored-system") "Result came from the wrong collection."
Assert-True ($payload.managed_by -eq "manifest-ingest") "Result was not produced by manifest ingestion."
Assert-True (-not ($payload.PSObject.Properties.Name -contains "roles")) "roles must not be persisted as a routing label."
Assert-True (-not ($payload.PSObject.Properties.Name -contains "incident_types")) "incident_types must not be persisted as a diagnosis label."

Write-Host "[4/4] Checking service-specific metadata..."
$serviceResults = @($search.result.points | Where-Object {
    $_.payload.services -contains "processing-service"
})
Assert-True ($serviceResults.Count -gt 0) "Expected at least one processing-service-related result."

Write-Host "Knowledge-base smoke test PASSED." -ForegroundColor Green
