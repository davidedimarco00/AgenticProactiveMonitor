[CmdletBinding()]
param(
    [int]$InitializerTimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Set-Location $PSScriptRoot

$MachineServices = @(
    "machine-01",
    "machine-02",
    "machine-03",
    "machine-04",
    "machine-05"
)

$InitializerServices = @(
    "opensearch-init",
    "opensearch-telemetry-init",
    "opensearch-dashboards-init",
    "opensearch-detectors-init"
)

function Invoke-DockerCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host "docker $($Arguments -join ' ')" -ForegroundColor DarkGray
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE: docker $($Arguments -join ' ')"
    }
}

function Wait-ComposeOneShot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Service,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $Deadline) {
        $ContainerId = (& docker compose ps -q $Service 2>$null | Out-String).Trim()

        if ($ContainerId) {
            $State = (& docker inspect --format "{{.State.Status}}" $ContainerId | Out-String).Trim()

            if ($State -eq "exited") {
                $ExitCode = [int]((& docker inspect --format "{{.State.ExitCode}}" $ContainerId | Out-String).Trim())

                if ($ExitCode -ne 0) {
                    Write-Host "" 
                    Write-Host "Logs for failed service $Service" -ForegroundColor Red
                    & docker compose logs --tail 300 $Service
                    throw "$Service exited with code $ExitCode"
                }

                Write-Host "$Service completed successfully." -ForegroundColor Green
                return
            }
        }

        Start-Sleep -Seconds 2
    }

    & docker compose logs --tail 300 $Service
    throw "Timed out waiting for $Service after $TimeoutSeconds seconds"
}

Write-Host "Validating Docker Compose configuration..." -ForegroundColor Cyan
Invoke-DockerCommand -Arguments @("compose", "config", "--quiet")

Write-Host "Stopping monitored machines and previous initializer containers..." -ForegroundColor Cyan
& docker compose stop @MachineServices @InitializerServices 2>$null

Write-Host "Removing only one-shot initializer containers..." -ForegroundColor Cyan
& docker compose rm --force --stop @InitializerServices 2>$null

Write-Host "Rebuilding monitored-machine images with the current Telegraf and Fluent Bit configuration..." -ForegroundColor Cyan
Invoke-DockerCommand -Arguments (@("compose", "build") + $MachineServices)

Write-Host "Applying OpenSearch templates and removing obsolete empty bootstrap indexes..." -ForegroundColor Cyan
Invoke-DockerCommand -Arguments @(
    "compose", "up", "-d", "--force-recreate", "opensearch-init"
)
Wait-ComposeOneShot -Service "opensearch-init" -TimeoutSeconds $InitializerTimeoutSeconds

Write-Host "Starting the five monitored machines..." -ForegroundColor Cyan
Invoke-DockerCommand -Arguments (@(
    "compose", "up", "-d", "--force-recreate"
) + $MachineServices)

Write-Host "Validating all Telegraf measurements and Fluent Bit logs..." -ForegroundColor Cyan
Invoke-DockerCommand -Arguments @(
    "compose", "up", "-d", "--force-recreate", "opensearch-telemetry-init"
)
Wait-ComposeOneShot -Service "opensearch-telemetry-init" -TimeoutSeconds $InitializerTimeoutSeconds

Write-Host "Refreshing Dashboards data views and provisioning CPU/RAM detectors..." -ForegroundColor Cyan
Invoke-DockerCommand -Arguments @(
    "compose", "up", "-d", "--force-recreate",
    "opensearch-dashboards-init", "opensearch-detectors-init"
)
Wait-ComposeOneShot -Service "opensearch-dashboards-init" -TimeoutSeconds $InitializerTimeoutSeconds
Wait-ComposeOneShot -Service "opensearch-detectors-init" -TimeoutSeconds $InitializerTimeoutSeconds

Write-Host "" 
Write-Host "Observability infrastructure restarted successfully." -ForegroundColor Green
Write-Host "OpenSearch and its data volume were preserved." -ForegroundColor Green
Write-Host "" 

& docker compose ps -a `
    opensearch `
    opensearch-dashboards `
    @MachineServices `
    @InitializerServices

Write-Host "" 
Write-Host "Open http://localhost:5601, press Ctrl+F5, select Last 15 minutes," -ForegroundColor Yellow
Write-Host "and use DQL: measurement_name: cpu AND cpu.usage_active:*" -ForegroundColor Yellow
