$ErrorActionPreference = "Stop"

$container = "data-service"

Write-Host "Starting scenario: data-service-down"
Write-Host "Stopping container '$container'..."

docker stop $container | Out-Null

Write-Host "Scenario active. The data service is unavailable."
Write-Host "Run .\infrastructure\scenarios\data-service-down\stop.ps1 to restore it."
