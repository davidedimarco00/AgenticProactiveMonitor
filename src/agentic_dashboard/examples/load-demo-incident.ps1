param(
    [string]$IncidentFile = (Join-Path $PSScriptRoot "demo-incident.json")
)

$ErrorActionPreference = "Stop"
$InfrastructureDir = Resolve-Path (Join-Path $PSScriptRoot "..\..\infrastructure")
$IncidentPath = Resolve-Path $IncidentFile

Write-Host "Loading mock incident from: $IncidentPath"
Write-Host "The seed writes directly through the backend MongoDB repository; no operator POST endpoint is used."

Push-Location $InfrastructureDir
try {
    $json = Get-Content $IncidentPath -Raw
    $json | docker compose exec -T agentic-backend python -m agentic_system.demo_seed
    if ($LASTEXITCODE -ne 0) {
        throw "Demo incident seed failed with exit code $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "Verifying the public read API..."
    $incident = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8082/api/v1/incidents/DEMO-CPU-001"

    Write-Host "API returned incident: $($incident.incident_id)"
    Write-Host "Status: $($incident.status)"
    Write-Host "Affected entity: $($incident.entity)"
    Write-Host "Timeline events: $($incident.timeline.Count)"
    Write-Host ""
    Write-Host "Open Swagger:  http://127.0.0.1:8082/docs"
    Write-Host "Open dashboard: http://127.0.0.1:5050/incidents/DEMO-CPU-001"
    Write-Host "PDF report:     http://127.0.0.1:5050/incidents/DEMO-CPU-001/report.pdf"
}
finally {
    Pop-Location
}
