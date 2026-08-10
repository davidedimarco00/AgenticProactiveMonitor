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

    [ValidateRange(2, 20)]
    [int]$DetectorWaitMinutes = 10,

    [ValidateRange(0, 30)]
    [int]$RecoveryMinutes = 10,

    [ValidateRange(1, 8)]
    [int]$CpuWorkers = 6,

    [ValidateRange(64, 2048)]
    [int]$MemoryTotalMB = 1024,

    [ValidateRange(1, 512)]
    [int]$MemoryStepMB = 64,

    [ValidateRange(1, 60)]
    [int]$MemoryStepSeconds = 10,

    [ValidateRange(1, 2000)]
    [int]$NetworkDelayMs = 400,

    [ValidateRange(0, 1000)]
    [int]$NetworkJitterMs = 50,

    [ValidateRange(1, 30000)]
    [int]$ApplicationDelayMs = 2000
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($MemoryStepMB -gt $MemoryTotalMB) {
    throw "MemoryStepMB cannot be greater than MemoryTotalMB."
}

$testsRoot = $PSScriptRoot
$infrastructureRoot = (Resolve-Path (Join-Path $testsRoot "..")).Path
$scenariosRoot = Join-Path $infrastructureRoot "scenarios"
$resetScript = Join-Path $scenariosRoot "reset-to-base.ps1"
$resultsRoot = Join-Path $testsRoot "results"

$script:results = New-Object System.Collections.Generic.List[object]
$script:experiments = New-Object System.Collections.Generic.List[object]

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

function Convert-EpochMillisecondsToIso {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    try {
        $epochMs = [int64]$Value
        if ($epochMs -le 0) {
            return $null
        }
        return [DateTimeOffset]::FromUnixTimeMilliseconds($epochMs).UtcDateTime.ToString("o")
    }
    catch {
        return $null
    }
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

    $jobDetail = Invoke-RestMethod `
        -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/$detectorId`?job=true" `
        -Method Get

    $enabledTimeMs = $null
    if ($jobDetail.PSObject.Properties.Name -contains "anomaly_detector_job") {
        $job = $jobDetail.anomaly_detector_job
        if ($null -ne $job -and $job.PSObject.Properties.Name -contains "enabled_time") {
            $enabledTimeMs = [int64]$job.enabled_time
        }
    }

    $runningMinutes = $null
    if ($null -ne $enabledTimeMs -and $enabledTimeMs -gt 0) {
        $runningMinutes = [math]::Round(
            ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $enabledTimeMs) / 60000.0,
            2
        )
    }

    return [pscustomobject]@{
        Name = $Name
        Id = $detectorId
        Type = $detectorType
        State = $state
        EnabledTimeMs = $enabledTimeMs
        EnabledAtUtc = Convert-EpochMillisecondsToIso -Value $enabledTimeMs
        RunningMinutes = $runningMinutes
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

function Add-ExperimentObservation {
    param(
        [string]$Scenario,
        [object]$Detector,
        [object]$Anomaly,
        [DateTimeOffset]$FaultStartedAt,
        [string]$MetricField,
        [object]$BaselineMetricValue,
        [object]$MaxMetricValue,
        [string]$MetricUnit,
        [string]$FaultParameters,
        [string]$FailureReason = ""
    )

    $detected = $null -ne $Anomaly
    $executionStartMs = $null
    $dataEndTimeMs = $null
    $grade = $null
    $confidence = $null
    $anomalyScore = $null

    if ($detected) {
        if ($Anomaly.PSObject.Properties.Name -contains "execution_start_time") {
            $executionStartMs = [int64]$Anomaly.execution_start_time
        }
        if ($Anomaly.PSObject.Properties.Name -contains "data_end_time") {
            $dataEndTimeMs = [int64]$Anomaly.data_end_time
        }
        if ($Anomaly.PSObject.Properties.Name -contains "anomaly_grade") {
            $grade = [math]::Round([double]$Anomaly.anomaly_grade, 6)
        }
        if ($Anomaly.PSObject.Properties.Name -contains "confidence") {
            $confidence = [math]::Round([double]$Anomaly.confidence, 6)
        }
        if ($Anomaly.PSObject.Properties.Name -contains "anomaly_score") {
            $anomalyScore = [math]::Round([double]$Anomaly.anomaly_score, 6)
        }
    }

    $runningMinutesAtFaultStart = $null
    if ($null -ne $Detector.EnabledTimeMs -and $Detector.EnabledTimeMs -gt 0) {
        $runningMinutesAtFaultStart = [math]::Round(
            ($FaultStartedAt.ToUnixTimeMilliseconds() - [int64]$Detector.EnabledTimeMs) / 60000.0,
            2
        )
    }

    $runningMinutesAtAnomaly = $null
    if ($detected -and $null -ne $executionStartMs -and $null -ne $Detector.EnabledTimeMs) {
        $runningMinutesAtAnomaly = [math]::Round(
            ($executionStartMs - [int64]$Detector.EnabledTimeMs) / 60000.0,
            2
        )
    }

    $detectionLatencySeconds = $null
    if ($detected -and $null -ne $executionStartMs) {
        $detectionLatencySeconds = [math]::Round(
            ($executionStartMs - $FaultStartedAt.ToUnixTimeMilliseconds()) / 1000.0,
            3
        )
    }

    $baselineRounded = $null
    if ($null -ne $BaselineMetricValue) {
        $baselineRounded = [math]::Round([double]$BaselineMetricValue, 6)
    }

    $maxRounded = $null
    if ($null -ne $MaxMetricValue) {
        $maxRounded = [math]::Round([double]$MaxMetricValue, 6)
    }

    $record = [pscustomobject]@{
        observed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        scenario = $Scenario
        detected = $detected
        detector_name = $Detector.Name
        detector_id = $Detector.Id
        detector_type = $Detector.Type
        detector_enabled_at_utc = $Detector.EnabledAtUtc
        detector_running_minutes_at_fault_start = $runningMinutesAtFaultStart
        detector_running_minutes_at_anomaly = $runningMinutesAtAnomaly
        fault_started_at_utc = $FaultStartedAt.UtcDateTime.ToString("o")
        anomaly_execution_time_utc = Convert-EpochMillisecondsToIso -Value $executionStartMs
        anomaly_data_end_time_utc = Convert-EpochMillisecondsToIso -Value $dataEndTimeMs
        anomaly_grade = $grade
        confidence = $confidence
        anomaly_score = $anomalyScore
        detection_latency_seconds = $detectionLatencySeconds
        metric_field = $MetricField
        baseline_metric_value = $baselineRounded
        max_metric_value = $maxRounded
        metric_unit = $MetricUnit
        fault_parameters = $FaultParameters
        failure_reason = $FailureReason
    }

    $script:experiments.Add($record) | Out-Null
    return $record
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

function Get-MetricStats {
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
                avg_value = @{
                    avg = @{
                        field = $Field
                    }
                }
                max_value = @{
                    max = @{
                        field = $Field
                    }
                }
            }
        }

    return [pscustomobject]@{
        Average = $response.aggregations.avg_value.value
        Max = $response.aggregations.max_value.value
    }
}

function Wait-ForAnomaly {
    param(
        [string]$DetectorId,
        [DateTimeOffset]$FaultStartedAt,
        [int]$TimeoutMinutes = 10
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

function Assert-NoControlledFaults {
    $checks = @(
        @{
            Container = "processing-service"
            Command = "test ! -s /var/run/monitored-faults/cpu-spike.pid"
            Message = "CPU spike marker is still active on processing-service."
        },
        @{
            Container = "processing-service"
            Command = "test ! -s /var/run/monitored-faults/processing-delay-ms"
            Message = "Application latency marker is still active on processing-service."
        },
        @{
            Container = "worker-service"
            Command = "test ! -s /var/run/monitored-faults/memory-leak.pid"
            Message = "Memory leak marker is still active on worker-service."
        }
    )

    foreach ($check in $checks) {
        docker exec $check.Container sh -c $check.Command 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw $check.Message
        }
    }

    docker exec api-gateway sh -c "tc qdisc show | grep -q netem" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        throw "A netem qdisc is still active on api-gateway."
    }
}

function Prepare-DetectorExperiment {
    param([string]$DetectorName)

    $null = Assert-DetectorReady -Name $DetectorName
    Reset-ToBase

    if ($RecoveryMinutes -gt 0) {
        Write-Host "Waiting $RecoveryMinutes minute(s) of clean normal traffic before fault injection..."
        Start-Sleep -Seconds ($RecoveryMinutes * 60)
    }

    Assert-NoControlledFaults
    return Assert-DetectorReady -Name $DetectorName
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

    Assert-NoControlledFaults
    Add-Result -Name "fault-state" -Status PASS -Detail "No controlled CPU, RAM, application-latency, or netem fault is active."

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
    $detector = Prepare-DetectorExperiment -DetectorName "CPU-processing-service"
    $baseline = Get-MetricStats `
        -HostId "processing-service" `
        -Measurement "docker_container_cpu" `
        -Field "docker_container_cpu.usage_percent" `
        -From ([DateTimeOffset]::UtcNow.AddMinutes(-5))

    $faultStartedAt = [DateTimeOffset]::UtcNow
    $faultParameters = "workers=$CpuWorkers"

    try {
        & (Join-Path $scenariosRoot "cpu-spike\start.ps1") -Workers $CpuWorkers

        $anomaly = Wait-ForAnomaly `
            -DetectorId $detector.Id `
            -FaultStartedAt $faultStartedAt `
            -TimeoutMinutes $DetectorWaitMinutes

        $faultStats = Get-MetricStats `
            -HostId "processing-service" `
            -Measurement "docker_container_cpu" `
            -Field "docker_container_cpu.usage_percent" `
            -From $faultStartedAt

        $baselineCpu = if ($null -eq $baseline.Average) { 0.0 } else { [double]$baseline.Average }
        $maxCpu = $faultStats.Max
        $minimumExpected = [math]::Max(100.0, $baselineCpu + 100.0)

        if ($null -eq $maxCpu -or [double]$maxCpu -lt $minimumExpected) {
            $reason = "CPU fault telemetry is too weak. baseline_avg=$baselineCpu%, max=$maxCpu%, expected_at_least=$minimumExpected%."
            $null = Add-ExperimentObservation -Scenario "cpu-spike" -Detector $detector -Anomaly $anomaly -FaultStartedAt $faultStartedAt -MetricField "docker_container_cpu.usage_percent" -BaselineMetricValue $baselineCpu -MaxMetricValue $maxCpu -MetricUnit "%" -FaultParameters $faultParameters -FailureReason $reason
            throw $reason
        }

        if ($null -eq $anomaly) {
            $reason = "CPU detector did not emit anomaly_grade > 0 within $DetectorWaitMinutes minute(s), although the CPU fault was confirmed by telemetry."
            $null = Add-ExperimentObservation -Scenario "cpu-spike" -Detector $detector -Anomaly $null -FaultStartedAt $faultStartedAt -MetricField "docker_container_cpu.usage_percent" -BaselineMetricValue $baselineCpu -MaxMetricValue $maxCpu -MetricUnit "%" -FaultParameters $faultParameters -FailureReason $reason
            throw $reason
        }

        $experiment = Add-ExperimentObservation -Scenario "cpu-spike" -Detector $detector -Anomaly $anomaly -FaultStartedAt $faultStartedAt -MetricField "docker_container_cpu.usage_percent" -BaselineMetricValue $baselineCpu -MaxMetricValue $maxCpu -MetricUnit "%" -FaultParameters $faultParameters

        Add-Result `
            -Name "cpu-spike" `
            -Status PASS `
            -Detail ("grade={0:N3}, confidence={1:N3}, detector_running={2:N2} min, baseline_cpu={3:N2}%, max_cpu={4:N2}%" -f [double]$anomaly.anomaly_grade, [double]$anomaly.confidence, [double]$experiment.detector_running_minutes_at_anomaly, $baselineCpu, [double]$maxCpu)
    }
    finally {
        Reset-ToBase
    }
}

function Test-MemoryLeak {
    Write-Step "Memory leak -> OpenSearch anomaly"
    $detector = Prepare-DetectorExperiment -DetectorName "RAM-worker-service"
    $baseline = Get-MetricStats `
        -HostId "worker-service" `
        -Measurement "docker_container_mem" `
        -Field "docker_container_mem.usage_percent" `
        -From ([DateTimeOffset]::UtcNow.AddMinutes(-5))

    $faultStartedAt = [DateTimeOffset]::UtcNow
    $faultParameters = "total_mb=$MemoryTotalMB;step_mb=$MemoryStepMB;step_seconds=$MemoryStepSeconds"

    try {
        & (Join-Path $scenariosRoot "memory-leak\start.ps1") `
            -TotalMB $MemoryTotalMB `
            -StepMB $MemoryStepMB `
            -StepSeconds $MemoryStepSeconds

        $anomaly = Wait-ForAnomaly `
            -DetectorId $detector.Id `
            -FaultStartedAt $faultStartedAt `
            -TimeoutMinutes $DetectorWaitMinutes

        $faultStats = Get-MetricStats `
            -HostId "worker-service" `
            -Measurement "docker_container_mem" `
            -Field "docker_container_mem.usage_percent" `
            -From $faultStartedAt

        $baselineRam = if ($null -eq $baseline.Average) { 0.0 } else { [double]$baseline.Average }
        $maxRam = $faultStats.Max
        $minimumExpected = $baselineRam + 5.0

        if ($null -eq $maxRam -or [double]$maxRam -lt $minimumExpected) {
            $reason = "RAM fault telemetry is too weak. baseline_avg=$baselineRam%, max=$maxRam%, expected_at_least=$minimumExpected%."
            $null = Add-ExperimentObservation -Scenario "memory-leak" -Detector $detector -Anomaly $anomaly -FaultStartedAt $faultStartedAt -MetricField "docker_container_mem.usage_percent" -BaselineMetricValue $baselineRam -MaxMetricValue $maxRam -MetricUnit "%" -FaultParameters $faultParameters -FailureReason $reason
            throw $reason
        }

        if ($null -eq $anomaly) {
            $reason = "RAM detector did not emit anomaly_grade > 0 within $DetectorWaitMinutes minute(s), although the memory fault was confirmed by telemetry."
            $null = Add-ExperimentObservation -Scenario "memory-leak" -Detector $detector -Anomaly $null -FaultStartedAt $faultStartedAt -MetricField "docker_container_mem.usage_percent" -BaselineMetricValue $baselineRam -MaxMetricValue $maxRam -MetricUnit "%" -FaultParameters $faultParameters -FailureReason $reason
            throw $reason
        }

        $experiment = Add-ExperimentObservation -Scenario "memory-leak" -Detector $detector -Anomaly $anomaly -FaultStartedAt $faultStartedAt -MetricField "docker_container_mem.usage_percent" -BaselineMetricValue $baselineRam -MaxMetricValue $maxRam -MetricUnit "%" -FaultParameters $faultParameters

        Add-Result `
            -Name "memory-leak" `
            -Status PASS `
            -Detail ("grade={0:N3}, confidence={1:N3}, detector_running={2:N2} min, baseline_ram={3:N2}%, max_ram={4:N2}%" -f [double]$anomaly.anomaly_grade, [double]$anomaly.confidence, [double]$experiment.detector_running_minutes_at_anomaly, $baselineRam, [double]$maxRam)
    }
    finally {
        Reset-ToBase
    }
}

function Test-NetworkLatency {
    Write-Step "Network latency -> OpenSearch anomaly"
    $detector = Prepare-DetectorExperiment -DetectorName "NETLAT-api-gateway-processing-service"
    $baseline = Get-MetricStats `
        -HostId "api-gateway" `
        -Measurement "network_service_latency" `
        -Field "network_service_latency.response_time" `
        -From ([DateTimeOffset]::UtcNow.AddMinutes(-5))

    $faultStartedAt = [DateTimeOffset]::UtcNow
    $faultParameters = "delay_ms=$NetworkDelayMs;jitter_ms=$NetworkJitterMs"

    try {
        & (Join-Path $scenariosRoot "network-latency\start.ps1") `
            -DelayMs $NetworkDelayMs `
            -JitterMs $NetworkJitterMs

        $anomaly = Wait-ForAnomaly `
            -DetectorId $detector.Id `
            -FaultStartedAt $faultStartedAt `
            -TimeoutMinutes $DetectorWaitMinutes

        $faultStats = Get-MetricStats `
            -HostId "api-gateway" `
            -Measurement "network_service_latency" `
            -Field "network_service_latency.response_time" `
            -From $faultStartedAt

        $baselineLatency = if ($null -eq $baseline.Average) { 0.0 } else { [double]$baseline.Average }
        $maxLatency = $faultStats.Max
        $minimumExpected = $baselineLatency + 0.3

        if ($null -eq $maxLatency -or [double]$maxLatency -lt $minimumExpected) {
            $reason = "Network fault telemetry is too weak. baseline_avg=$baselineLatency s, max=$maxLatency s, expected_at_least=$minimumExpected s."
            $null = Add-ExperimentObservation -Scenario "network-latency" -Detector $detector -Anomaly $anomaly -FaultStartedAt $faultStartedAt -MetricField "network_service_latency.response_time" -BaselineMetricValue $baselineLatency -MaxMetricValue $maxLatency -MetricUnit "s" -FaultParameters $faultParameters -FailureReason $reason
            throw $reason
        }

        if ($null -eq $anomaly) {
            $reason = "Network-latency detector did not emit anomaly_grade > 0 within $DetectorWaitMinutes minute(s), although the network fault was confirmed by telemetry."
            $null = Add-ExperimentObservation -Scenario "network-latency" -Detector $detector -Anomaly $null -FaultStartedAt $faultStartedAt -MetricField "network_service_latency.response_time" -BaselineMetricValue $baselineLatency -MaxMetricValue $maxLatency -MetricUnit "s" -FaultParameters $faultParameters -FailureReason $reason
            throw $reason
        }

        $experiment = Add-ExperimentObservation -Scenario "network-latency" -Detector $detector -Anomaly $anomaly -FaultStartedAt $faultStartedAt -MetricField "network_service_latency.response_time" -BaselineMetricValue $baselineLatency -MaxMetricValue $maxLatency -MetricUnit "s" -FaultParameters $faultParameters

        Add-Result `
            -Name "network-latency" `
            -Status PASS `
            -Detail ("grade={0:N3}, confidence={1:N3}, detector_running={2:N2} min, baseline_latency={3:N3}s, max_response_time={4:N3}s" -f [double]$anomaly.anomaly_grade, [double]$anomaly.confidence, [double]$experiment.detector_running_minutes_at_anomaly, $baselineLatency, [double]$maxLatency)
    }
    finally {
        Reset-ToBase
    }
}

function Test-HighLatency {
    Write-Step "Application high latency"

    try {
        Reset-ToBase
        & (Join-Path $scenariosRoot "high-latency\start.ps1") -DelayMs $ApplicationDelayMs
        Start-Sleep -Seconds 2

        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $status = Get-HttpStatus -Url "$GatewayUrl/health"
        $stopwatch.Stop()

        if ($status -ne 200) {
            throw "Gateway health request returned HTTP $status during controlled application latency."
        }

        $minimumVisibleSeconds = [math]::Max(0.5, ($ApplicationDelayMs / 1000.0) * 0.75)
        if ($stopwatch.Elapsed.TotalSeconds -lt $minimumVisibleSeconds) {
            throw "Controlled processing delay was not visible end-to-end. Elapsed: $($stopwatch.Elapsed.TotalSeconds)s"
        }

        Add-Result `
            -Name "high-latency" `
            -Status PASS `
            -Detail ("HTTP 200 with end-to-end latency {0:N2}s for configured delay {1}ms" -f $stopwatch.Elapsed.TotalSeconds, $ApplicationDelayMs)
    }
    finally {
        Reset-ToBase
    }
}

function Test-DataServiceDown {
    Write-Step "Data service unavailable"

    try {
        Reset-ToBase
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
    $historyPath = Join-Path $resultsRoot "detector-confidence-history.csv"
    $reportResults = $script:results.ToArray()
    $reportExperiments = $script:experiments.ToArray()

    $report = [pscustomobject]@{
        selected_test = $Test
        generated_at = [DateTimeOffset]::UtcNow.ToString("o")
        parameters = [pscustomobject]@{
            detector_wait_minutes = $DetectorWaitMinutes
            recovery_minutes = $RecoveryMinutes
            cpu_workers = $CpuWorkers
            memory_total_mb = $MemoryTotalMB
            memory_step_mb = $MemoryStepMB
            memory_step_seconds = $MemoryStepSeconds
            network_delay_ms = $NetworkDelayMs
            network_jitter_ms = $NetworkJitterMs
            application_delay_ms = $ApplicationDelayMs
        }
        results = $reportResults
        detector_experiments = $reportExperiments
    }

    $report |
        ConvertTo-Json -Depth 20 |
        Set-Content -Path $reportPath -Encoding UTF8

    if ($reportExperiments.Count -gt 0) {
        if (Test-Path $historyPath) {
            $reportExperiments |
                Export-Csv -Path $historyPath -NoTypeInformation -Encoding UTF8 -Append
        }
        else {
            $reportExperiments |
                Export-Csv -Path $historyPath -NoTypeInformation -Encoding UTF8
        }
    }

    Write-Host ""
    Write-Host "Report: $reportPath"
    if ($reportExperiments.Count -gt 0) {
        Write-Host "Experiment history: $historyPath"
    }
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
            Test-MemoryLeak
            Test-NetworkLatency
            Test-HighLatency
            Test-DataServiceDown
        }
    }
}
catch {
    $failed = $true
    Add-Result -Name $Test -Status FAIL -Detail $_.Exception.Message
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
