param(
    [string]$OpenSearchUrl = "http://localhost:9200",
    [ValidateRange(1000, 7000)]
    [int]$DelayMs = 5000,
    [ValidateRange(10, 60)]
    [int]$ObservationWaitSeconds = 25
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$infrastructureRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scenariosRoot = Join-Path $infrastructureRoot "scenarios"
$resetScript = Join-Path $scenariosRoot "reset-to-base.ps1"
$startScript = Join-Path $scenariosRoot "high-latency\start.ps1"
$stopScript = Join-Path $scenariosRoot "high-latency\stop.ps1"

function Invoke-OpenSearchPost {
    param(
        [string]$Path,
        [hashtable]$Body
    )

    $json = $Body | ConvertTo-Json -Depth 30 -Compress
    Invoke-RestMethod `
        -Uri "$OpenSearchUrl$Path" `
        -Method Post `
        -ContentType "application/json" `
        -Body $json
}

function Get-NestedValue {
    param(
        [object]$Object,
        [string]$Path
    )

    $value = $Object
    foreach ($segment in $Path.Split('.')) {
        if ($null -eq $value) {
            return $null
        }
        $property = $value.PSObject.Properties[$segment]
        if ($null -eq $property) {
            return $null
        }
        $value = $property.Value
    }
    return $value
}

function Get-LatestMetric {
    param(
        [string]$Measurement,
        [string]$Field,
        [DateTimeOffset]$From
    )

    $response = Invoke-OpenSearchPost `
        -Path "/metrics-api-gateway-*/_search?ignore_unavailable=true" `
        -Body @{
            size = 1
            sort = @(
                @{
                    "@timestamp" = @{
                        order = "desc"
                    }
                }
            )
            query = @{
                bool = @{
                    filter = @(
                        @{
                            range = @{
                                "@timestamp" = @{
                                    gte = $From.UtcDateTime.ToString("o")
                                }
                            }
                        },
                        @{
                            term = @{
                                measurement_name = $Measurement
                            }
                        },
                        @{
                            exists = @{
                                field = $Field
                            }
                        }
                    )
                }
            }
        }

    $hits = @($response.hits.hits)
    if ($hits.Count -eq 0) {
        throw "No fresh '$Measurement' document with field '$Field' was found."
    }

    $source = $hits[0]._source
    $value = Get-NestedValue -Object $source -Path $Field
    if ($null -eq $value) {
        throw "Metric '$Field' was not readable from the latest '$Measurement' document."
    }

    [pscustomobject]@{
        Measurement = $Measurement
        Field = $Field
        Value = [double]$value
        Timestamp = [string]$source.'@timestamp'
    }
}

Write-Host "Resetting monitored system..." -ForegroundColor Cyan
& $resetScript

try {
    Write-Host "Starting application-level delay: ${DelayMs}ms" -ForegroundColor Cyan
    & $startScript -DelayMs $DelayMs
    $faultStartedAt = [DateTimeOffset]::UtcNow

    Write-Host "Waiting ${ObservationWaitSeconds}s for fresh Telegraf samples..." -ForegroundColor Cyan
    Start-Sleep -Seconds $ObservationWaitSeconds

    $transport = Get-LatestMetric `
        -Measurement "network_transport_latency" `
        -Field "network_transport_latency.response_time" `
        -From $faultStartedAt

    $application = Get-LatestMetric `
        -Measurement "application_service_latency" `
        -Field "application_service_latency.response_time" `
        -From $faultStartedAt

    Write-Host ""
    Write-Host ("TCP transport response_time: {0:N3}s at {1}" -f $transport.Value, $transport.Timestamp)
    Write-Host ("HTTP application response_time: {0:N3}s at {1}" -f $application.Value, $application.Timestamp)

    $minimumExpectedApplicationSeconds = [math]::Max(0.8, ($DelayMs / 1000.0) * 0.8)

    if ($application.Value -lt $minimumExpectedApplicationSeconds) {
        throw (
            "Application latency did not reflect the injected delay. " +
            "Expected at least $minimumExpectedApplicationSeconds s, observed $($application.Value) s."
        )
    }

    if ($transport.Value -ge $application.Value) {
        throw (
            "Transport latency should remain below application latency for the high-latency scenario. " +
            "Transport=$($transport.Value)s Application=$($application.Value)s."
        )
    }

    Write-Host ""
    Write-Host "PASS: application delay is visible in application_service_latency while TCP transport remains a separate signal." -ForegroundColor Green
}
finally {
    Write-Host "Restoring base state..." -ForegroundColor Cyan
    & $stopScript
    & $resetScript
}
