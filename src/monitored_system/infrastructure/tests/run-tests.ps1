param(
    [ValidateSet(
        "preflight",
        "cpu-spike",
        "memory-leak",
        "network-latency",
        "high-latency",
        "data-service-down",
        "all"
    )]
    [string]$Test = "preflight",

    [string]$OpenSearchUrl = "http://localhost:9200",
    [string]$GatewayUrl = "http://localhost:8080",

    [ValidateRange(2, 15)]
    [int]$DetectorWaitMinutes = 7,

    [ValidateRange(1, 15)]
    [int]$RecoveryMinutes = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$testsRoot = $PSScriptRoot
$infrastructureRoot = (Resolve-Path (Join-Path $testsRoot "..")).Path
$scenariosRoot = Join-Path $infrastructureRoot "scenarios"
$resetScript = Join-Path $scenariosRoot "reset-to-base.ps1"
$resultsRoot = Join-Path $testsRoot "results"

$script:results = New-Object System.Collections.Generic.List[object]

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Add-Result {
    param(
        [string]$Name,
        [ValidateSet("PASS", "FAIL")]
        [string]$Status,
        [string]$Detail
    )

    $script:results.Add([pscustomobject]@{
        test = $Name
        status = $Status
        detail = $Detail
        timestamp = [DateTimeOffset]::UtcNow.ToString("o")
    }) | Out-Null

    if ($Status -eq "PASS") {
        Write-Host "[PASS] $Name - $Detail" -ForegroundColor Green
    }
    else {
        Write-Host "[FAIL] $Name - $Detail" -ForegroundColor Red
    }
}

function Invoke-OpenSearchPost {
    param(
        [string]$Path,
        [hashtable]$Body
    )

    $json = $Body | ConvertTo-Json -Depth 30 -Compress
    return Invoke-RestMethod `
        -Uri "$OpenSearchUrl$Path" `
        -Method Post `
        -ContentType "application/json" `
        -Body $json
}

function Get-DetectorInfo {
    param([string]$Name)

    $search = Invoke-OpenSearchPost `
        -Path "/_plugins/_anomaly_detection/detectors/_search" `
        -Body @{
            size = 100
            _source = @("name")
            query = @{
                match_phrase = @{
                    name = $Name
                }
            }
        }

    $hit = @($search.hits.hits) |
        Where-Object { $_._source.name -eq $Name } |
        Select-Object -First 1

    if ($null -eq $hit) {
        throw "Detector '$Name' was not found."
    }

    $detectorId = [string]$hit._id

    $detail = Invoke-RestMethod `
        -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/$detectorId" `
        -Method Get

    $detectorType = "UNKNOWN"
    if ($detail.PSObject.Properties.Name -contains "anomaly_detector") {
        if ($detail.anomaly_detector.PSObject.Properties.Name -contains "detector_type") {
            $detectorType = [string]$detail.anomaly_detector.detector_type
        }
    }
    elseif ($detail.PSObject.Properties.Name -contains "detector_type") {
        $detectorType = [string]$detail.detector_type
    }

    $profile = Invoke-RestMethod `
        -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/$detectorId/_profile?_all=true" `
        -Method Get

    $state = "UNKNOWN"
    if ($profile.PSObject.Properties.Name -contains "state") {
        $state = [string]$profile.state
    }

    return [pscustomobject]@{
        Name = $Name
        Id = $detectorId
        Type = $detectorType
        State = $state
    }
}

function Assert-DetectorReady {
    param([string]$Name)

    $detector = Get-DetectorInfo -Name $Name

    if ($detector.Type -ne "SINGLE_ENTITY") {
        throw "Detector '$Name' is '$($detector.Type)' instead of SINGLE_ENTITY."
    }

    if ($detector.State -ne "RUNNING") {
        throw "Detector '$Name' is not RUNNING. Current state: $($detector.State)."
    }

    return $detector
}

function Get-IndexDocumentCount {
    param([string]$Pattern)

    try {
        $response = Invoke-RestMethod `
            -Uri "$OpenSearchUrl/$Pattern/_count?ignore_unavailable=true" `
            -Method Get
        return [int64]$response.count
    }
    catch {
        return 0
    }
}

function Get-MaxMetricValue {
    param(
        [string]$HostId,
        [string]$Measurement,
        [string]$Field,
        [DateTimeOffset]$From
    )

    $response = Invoke-OpenSearchPost `
        -Path "/metrics-$HostId-*/_search?ignore_unavailable=true" `
        -Body @{
            size = 0
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
            aggs = @{
                max_value = @{
                    max = @{
                        field = $Field
                    }
                }
            }
        }

    return $response.aggregations.max_value.value
}

function Wait-ForAnomaly {
    param(
        [string]$DetectorId,
        [DateTimeOffset]$FaultStartedAt,
        [int]$TimeoutMinutes = 7
    )

    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $faultStartMs = $FaultStartedAt.ToUnixTimeMilliseconds()

    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $response = Invoke-OpenSearchPost `
            -Path "/_plugins/_anomaly_detection/detectors/results/_search/" `
            -Body @{
                size = 1
                sort = @(
                    @{
                        execution_start_time = @{
                            order = "desc"
                        }
                    }
                )
                query = @{
                    bool = @{
                        filter = @(
                            @{
                                term = @{
                                    detector_id = $DetectorId
                                }
                            },
                            @{
                                range = @{
                                    anomaly_grade = @{
                                        gt = 0
                                    }
                                }
                            },
                            @{
                                range = @{
                                    data_end_time = @{
                                        gte = $faultStartMs
                                    }
                                }
                            }
                        )
                        must_not = @(
                            @{
                                exists = @{
                                    field = "task_id"
                                }
                            }
                        )
                    }
                }
            }

        $hits = @($response.hits.hits)
        if ($hits.Count -gt 0) {
            return $hits[0]._source
        }

        Write-Host "Waiting for anomaly result from detector $DetectorId..."
        Start-Sleep -Seconds 15
    }

    return $null
}

function Get-HttpStatus {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -UseBasicParsing -TimeoutSec 10
        return [int]$response.StatusCode
    }
    catch {
        if ($null -ne $_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        throw
    }
}

function Wait-ForGatewayHealthy {
    param([int]$TimeoutSeconds = 90)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            if ((Get-HttpStatus -Url "$GatewayUrl/health") -eq 200) {
                return $true
            }
        }
        catch {
            # Retry until timeout.
        }

        Start-Sleep -Seconds 3
    }

    return $false
}

function Reset-ToBase {
    Write-Host "Restoring base scenario..."
    & $resetScript

    if (-not (Wait-ForGatewayHealthy)) {
        throw "Gateway did not return to a healthy state after reset."
    }
}

function Wait-RecoveryWindow {
    Write-Host "Waiting $RecoveryMinutes minute(s) of normal traffic before the next detector test..."
    Start-Sleep -Seconds ($RecoveryMinutes * 60)
}

function Test-Preflight {
    Write-Step "Preflight"

    $containers = @(
        "traffic-generator",
        "api-gateway",
        "processing-service",
        "data-service",
        "worker-service"
    )

    foreach ($container in $containers) {
        $running = (docker inspect -f "{{.State.Running}}" $container 2>$null | Out-String).Trim()
        if ($running -ne "true") {
            throw "Container '$container' is not running."
        }
    }
    Add-Result -Name "containers" -Status PASS -Detail "All five monitored containers are running."

    if ((Get-HttpStatus -Url "$GatewayUrl/health") -ne 200) {
        throw "Gateway health endpoint is not returning HTTP 200."
    }
    Add-Result -Name "notes-health" -Status PASS -Detail "Gateway and downstream Notes services are healthy."

    foreach ($container in $containers) {
        $metricCount = Get-IndexDocumentCount -Pattern "metrics-$container-*"
        $logCount = Get-IndexDocumentCount -Pattern "logs-$container-*"

        if ($metricCount -le 0) {
            throw "No metrics found for '$container'."
        }
        if ($logCount -le 0) {
            throw "No logs found for '$container'."
        }
    }
    Add-Result -Name "telemetry" -Status PASS -Detail "Metrics and logs are present for all five monitored services."

    $expectedDetectors = @(
        "CPU-traffic-generator",
        "RAM-traffic-generator",
        "CPU-api-gateway",
        "RAM-api-gateway",
        "CPU-processing-service",
        "RAM-processing-service",
        "CPU-data-service",
        "RAM-data-service",
        "CPU-worker-service",
        "RAM-worker-service",
        "NETLAT-traffic-generator-api-gateway",
        "NETLAT-api-gateway-processing-service",
        "NETLAT-processing-service-data-service"
    )

    foreach ($name in $expectedDetectors) {
        $null = Assert-DetectorReady -Name $name
    }

    Add-Result `
        -Name "detectors" `
        -Status PASS `
        -Detail "All 13 expected detectors are RUNNING and SINGLE_ENTITY."
}

function Test-CpuSpike {
    Write-Step "CPU spike -> OpenSearch anomaly"
    $detector = Assert-DetectorReady -Name "CPU-processing-service"
    $faultStartedAt = [DateTimeOffset]::UtcNow

    try {
        & (Join-Path $scenariosRoot "cpu-spike\start.ps1") -Workers 4

        $anomaly = Wait-ForAnomaly `
            -DetectorId $detector.Id `
            -FaultStartedAt $faultStartedAt `
            -TimeoutMinutes $DetectorWaitMinutes

        if ($null -eq $anomaly) {
            throw "CPU detector did not emit anomaly_grade > 0 within $DetectorWaitMinutes minute(s)."
        }

        $maxCpu = Get-MaxMetricValue `
            -HostId "processing-service" `
            -Measurement "docker_container_cpu" `
            -Field "docker_container_cpu.usage_percent" `
            -From $faultStartedAt

        if ($null -eq $maxCpu -or [double]$maxCpu -lt 100) {
            throw "CPU telemetry did not show the expected spike. Max value: $maxCpu"
        }

        Add-Result `
            -Name "cpu-spike" `
            -Status PASS `
            -Detail ("grade={0:N3}, confidence={1:N3}, max_cpu={2:N2}%" -f [double]$anomaly.anomaly_grade, [double]$anomaly.confidence, [double]$maxCpu)
    }
    finally {
        Reset-ToBase
    }
}

function Test-MemoryLeak {
    Write-Step "Memory leak -> OpenSearch anomaly"
    $detector = Assert-DetectorReady -Name "RAM-worker-service"
    $faultStartedAt = [DateTimeOffset]::UtcNow

    try {
        & (Join-Path $scenariosRoot "memory-leak\start.ps1") `
            -TotalMB 512 `
            -StepMB 32 `
            -StepSeconds 2

        $anomaly = Wait-ForAnomaly `
            -DetectorId $detector.Id `
            -FaultStartedAt $faultStartedAt `
            -TimeoutMinutes $DetectorWaitMinutes

        if ($null -eq $anomaly) {
            throw "RAM detector did not emit anomaly_grade > 0 within $DetectorWaitMinutes minute(s)."
        }

        $maxRam = Get-MaxMetricValue `
            -HostId "worker-service" `
            -Measurement "docker_container_mem" `
            -Field "docker_container_mem.usage_percent" `
            -From $faultStartedAt

        if ($null -eq $maxRam) {
            throw "RAM telemetry is missing during the memory-leak scenario."
        }

        Add-Result `
            -Name "memory-leak" `
            -Status PASS `
            -Detail ("grade={0:N3}, confidence={1:N3}, max_ram={2:N2}%" -f [double]$anomaly.anomaly_grade, [double]$anomaly.confidence, [double]$maxRam)
    }
    finally {
        Reset-ToBase
    }
}

function Test-NetworkLatency {
    Write-Step "Network latency -> OpenSearch anomaly"
    $detector = Assert-DetectorReady -Name "NETLAT-api-gateway-processing-service"
    $faultStartedAt = [DateTimeOffset]::UtcNow

    try {
        & (Join-Path $scenariosRoot "network-latency\start.ps1") -DelayMs 250

        $anomaly = Wait-ForAnomaly `
            -DetectorId $detector.Id `
            -FaultStartedAt $faultStartedAt `
            -TimeoutMinutes $DetectorWaitMinutes

        if ($null -eq $anomaly) {
            throw "Network-latency detector did not emit anomaly_grade > 0 within $DetectorWaitMinutes minute(s)."
        }

        $maxLatency = Get-MaxMetricValue `
            -HostId "api-gateway" `
            -Measurement "network_service_latency" `
            -Field "network_service_latency.response_time" `
            -From $faultStartedAt

        if ($null -eq $maxLatency -or [double]$maxLatency -lt 0.2) {
            throw "Network latency telemetry did not show the injected delay. Max value: $maxLatency"
        }

        Add-Result `
            -Name "network-latency" `
            -Status PASS `
            -Detail ("grade={0:N3}, confidence={1:N3}, max_response_time={2:N3}s" -f [double]$anomaly.anomaly_grade, [double]$anomaly.confidence, [double]$maxLatency)
    }
    finally {
        Reset-ToBase
    }
}

function Test-HighLatency {
    Write-Step "Application high latency"

    try {
        & (Join-Path $scenariosRoot "high-latency\start.ps1") -DelayMs 2000
        Start-Sleep -Seconds 2

        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $status = Get-HttpStatus -Url "$GatewayUrl/health"
        $stopwatch.Stop()

        if ($status -ne 200) {
            throw "Gateway health request returned HTTP $status during controlled application latency."
        }

        if ($stopwatch.Elapsed.TotalSeconds -lt 1.5) {
            throw "Controlled processing delay was not visible end-to-end. Elapsed: $($stopwatch.Elapsed.TotalSeconds)s"
        }

        Add-Result `
            -Name "high-latency" `
            -Status PASS `
            -Detail ("HTTP 200 with end-to-end latency {0:N2}s" -f $stopwatch.Elapsed.TotalSeconds)
    }
    finally {
        Reset-ToBase
    }
}

function Test-DataServiceDown {
    Write-Step "Data service unavailable"

    try {
        & (Join-Path $scenariosRoot "data-service-down\start.ps1")
        Start-Sleep -Seconds 2

        $status = Get-HttpStatus -Url "$GatewayUrl/health"
        if ($status -ne 503) {
            throw "Expected HTTP 503 while data-service was stopped, received HTTP $status."
        }

        Add-Result `
            -Name "data-service-down" `
            -Status PASS `
            -Detail "Gateway propagated the downstream failure as HTTP 503."
    }
    finally {
        Reset-ToBase
    }
}

function Save-TestReport {
    if (-not (Test-Path $resultsRoot)) {
        New-Item -ItemType Directory -Path $resultsRoot | Out-Null
    }

    $timestamp = [DateTimeOffset]::Now.ToString("yyyyMMdd-HHmmss")
    $reportPath = Join-Path $resultsRoot "test-report-$timestamp.json"
    $reportResults = $script:results.ToArray()

    $report = [pscustomobject]@{
        selected_test = $Test
        generated_at = [DateTimeOffset]::UtcNow.ToString("o")
        results = $reportResults
    }

    $report |
        ConvertTo-Json -Depth 20 |
        Set-Content -Path $reportPath -Encoding UTF8

    Write-Host ""
    Write-Host "Report: $reportPath"
}

$failed = $false

try {
    switch ($Test) {
        "preflight" {
            Test-Preflight
        }
        "cpu-spike" {
            Test-CpuSpike
        }
        "memory-leak" {
            Test-MemoryLeak
        }
        "network-latency" {
            Test-NetworkLatency
        }
        "high-latency" {
            Test-HighLatency
        }
        "data-service-down" {
            Test-DataServiceDown
        }
        "all" {
            Test-Preflight

            Test-CpuSpike
            Wait-RecoveryWindow

            Test-MemoryLeak
            Wait-RecoveryWindow

            Test-NetworkLatency
            Wait-RecoveryWindow

            Test-HighLatency
            Test-DataServiceDown
        }
    }
}
catch {
    $failed = $true
    Add-Result -Name $Test -Status FAIL -Detail $_.Exception.Message

    try {
        Reset-ToBase
    }
    catch {
        Write-Warning "Automatic recovery also failed: $($_.Exception.Message)"
    }
}
finally {
    Save-TestReport
}

Write-Host ""
if ($failed) {
    Write-Host "MONITORED SYSTEM TEST SUITE: FAILED" -ForegroundColor Red
    exit 1
}

Write-Host "MONITORED SYSTEM TEST SUITE: PASSED" -ForegroundColor Green
exit 0
