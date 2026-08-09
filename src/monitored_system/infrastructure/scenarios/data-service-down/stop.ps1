$ErrorActionPreference = "Stop"

$container = "data-service"

Write-Host "Stopping scenario: data-service-down"
Write-Host "Starting container '$container'..."

docker start $container | Out-Null

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:8003/health" -Method Get -TimeoutSec 2
        if ($response.status -eq "ok") {
            Write-Host "Data service restored and healthy."
            exit 0
        }
    }
    catch {
        # Service may still be starting.
    }
} while ((Get-Date) -lt $deadline)

Write-Warning "The container started, but the health endpoint did not answer within 30 seconds."
