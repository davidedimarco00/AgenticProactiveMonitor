param(
    [string]$Campaign = "baseline",
    [string]$Scenario = "all",
    [int]$Repetitions = 0,
    [int]$RecoveryMinutes = -1,
    [string]$Profile = "",
    [switch]$PrepareEnvironment,
    [switch]$PreflightOnly,
    [switch]$SkipModelWarmup,
    [string]$OpenSearchUrl = "http://localhost:9200",
    [string]$BackendUrl = "http://localhost:8082",
    [string]$GatewayUrl = "http://localhost:8080",
    [string]$OllamaUrl = "http://localhost:11434"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$evaluationRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $evaluationRoot "..")).Path
$configRoot = Join-Path $evaluationRoot "config"
$resultsRoot = Join-Path $evaluationRoot "results"
$groundTruthPath = Join-Path $configRoot "ground-truth.json"
$modelProfilesPath = Join-Path $configRoot "model-profiles.json"
$campaignPath = Join-Path $configRoot "campaigns\$Campaign.json"
$infrastructureRoot = Join-Path $repoRoot "src\infrastructure"
$monitoredRoot = Join-Path $repoRoot "src\monitored_system"
$resetScript = Join-Path $monitoredRoot "infrastructure\scenarios\reset-to-base.ps1"
$scoreScript = Join-Path $evaluationRoot "analysis\score_runs.py"
$aggregateScript = Join-Path $evaluationRoot "analysis\aggregate_results.py"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Get-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Required file does not exist: $Path"
    }
    return Get-Content -Raw -Path $Path | ConvertFrom-Json
}

function Save-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Value | ConvertTo-Json -Depth 100 | Set-Content -Path $Path -Encoding UTF8
}

function Get-NamedProperty {
    param(
        [object]$Object,
        [string]$Name
    )
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        throw "Configuration entry '$Name' was not found."
    }
    return $property.Value
}

function ConvertTo-Hashtable {
    param([object]$Object)
    $table = @{}
    if ($null -eq $Object) {
        return $table
    }
    foreach ($property in $Object.PSObject.Properties) {
        $table[$property.Name] = $property.Value
    }
    return $table
}

function Assert-Command {
    param([string]$Name)
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' is not available in PATH."
    }
}

function Assert-EvaluationBranch {
    Push-Location $repoRoot
    try {
        $branch = (& git branch --show-current | Out-String).Trim()
        if ($branch -ne "evaluation") {
            throw "Evaluation must run on branch 'evaluation'. Current branch: '$branch'."
        }
        return (& git rev-parse HEAD | Out-String).Trim()
    }
    finally {
        Pop-Location
    }
}

function Invoke-JsonRequest {
    param(
        [string]$Uri,
        [ValidateSet("Get", "Post")]
        [string]$Method = "Get",
        [object]$Body = $null
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -TimeoutSec 30
    }
    $json = $Body | ConvertTo-Json -Depth 100 -Compress
    return Invoke-RestMethod -Uri $Uri -Method $Method -ContentType "application/json" -Body $json -TimeoutSec 60
}

function Wait-ForHttp {
    param(
        [string]$Uri,
        [int]$TimeoutSeconds = 180
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 10 | Out-Null
            return
        }
        catch {
            Start-Sleep -Seconds 3
        }
    }
    throw "Timed out waiting for $Uri"
}

function Wait-ForGatewayHealthy {
    param([int]$TimeoutSeconds = 120)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri "$GatewayUrl/health" -Method Get -UseBasicParsing -TimeoutSec 10
            if ([int]$response.StatusCode -eq 200) {
                return
            }
        }
        catch {
        }
        Start-Sleep -Seconds 3
    }
    throw "The monitored API gateway did not become healthy."
}

function Get-InstalledOllamaModels {
    $tags = Invoke-JsonRequest -Uri "$OllamaUrl/api/tags"
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($model in @($tags.models)) {
        $name = [string]($model.name)
        if ([string]::IsNullOrWhiteSpace($name) -and $model.PSObject.Properties.Name -contains "model") {
            $name = [string]$model.model
        }
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            $names.Add($name) | Out-Null
        }
    }
    return @($names)
}

function Assert-ModelsAvailable {
    param([object]$ModelProfile)
    $installed = @(Get-InstalledOllamaModels)
    $required = @(
        [string]$ModelProfile.reasoning_model,
        [string]$ModelProfile.tool_model,
        [string]$ModelProfile.embedding_model
    ) | Select-Object -Unique

    foreach ($model in $required) {
        if ($installed -notcontains $model) {
            throw "Required Ollama model '$model' is not installed. No fallback is allowed during evaluation. Installed models: $($installed -join ', ')"
        }
    }
}

function Warm-Models {
    param([object]$ModelProfile)
    if ($SkipModelWarmup) {
        return
    }

    Write-Step "Warming Ollama models"
    foreach ($model in @([string]$ModelProfile.reasoning_model, [string]$ModelProfile.tool_model) | Select-Object -Unique) {
        Invoke-JsonRequest -Uri "$OllamaUrl/api/chat" -Method Post -Body @{
            model = $model
            messages = @(
                @{ role = "user"; content = "Evaluation warm-up. Reply with OK." }
            )
            stream = $false
            options = @{ temperature = 0 }
        } | Out-Null
        Write-Host "Warmed model: $model"
    }

    Invoke-JsonRequest -Uri "$OllamaUrl/api/embed" -Method Post -Body @{
        model = [string]$ModelProfile.embedding_model
        input = @("evaluation warm-up")
    } | Out-Null
    Write-Host "Warmed embedding model: $($ModelProfile.embedding_model)"
}

function Set-ModelEnvironment {
    param([object]$ModelProfile)
    $env:OLLAMA_CHAT_MODEL = [string]$ModelProfile.reasoning_model
    $env:OLLAMA_TOOL_MODEL = [string]$ModelProfile.tool_model
    $env:OLLAMA_EMBEDDING_MODEL = [string]$ModelProfile.embedding_model
}

function Restart-AgenticBackend {
    param(
        [object]$ModelProfile,
        [switch]$Build
    )
    Set-ModelEnvironment -ModelProfile $ModelProfile
    Push-Location $infrastructureRoot
    try {
        if ($Build) {
            docker compose up -d --build --force-recreate agentic-backend
        }
        else {
            docker compose up -d --force-recreate agentic-backend
        }
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose could not start the agentic backend."
        }
    }
    finally {
        Pop-Location
    }
    Wait-ForHttp -Uri "$BackendUrl/health" -TimeoutSeconds 300
}

function Reset-MonitoredSystem {
    Write-Host "Restoring monitored system to normal base state..."
    & $resetScript
    if ($LASTEXITCODE -ne 0) {
        throw "reset-to-base.ps1 failed."
    }
    Wait-ForGatewayHealthy
}

function Prepare-Environment {
    param([object]$ModelProfile)
    Write-Step "Preparing infrastructure"
    Restart-AgenticBackend -ModelProfile $ModelProfile -Build
    Reset-MonitoredSystem

    Push-Location $infrastructureRoot
    try {
        docker compose up -d --force-recreate opensearch-detectors-init
        if ($LASTEXITCODE -ne 0) {
            throw "Could not start OpenSearch detector initialisation."
        }
    }
    finally {
        Pop-Location
    }
}

function Get-ExpectedDetectorNames {
    $hosts = @("traffic-generator", "api-gateway", "processing-service", "data-service", "worker-service")
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($hostName in $hosts) {
        $names.Add("CPU-$hostName") | Out-Null
        $names.Add("RAM-$hostName") | Out-Null
    }
    foreach ($name in @(
        "NETLAT-traffic-generator-api-gateway",
        "NETLAT-api-gateway-processing-service",
        "NETLAT-processing-service-data-service",
        "APPLAT-traffic-generator-api-gateway",
        "APPLAT-api-gateway-processing-service",
        "APPLAT-processing-service-data-service"
    )) {
        $names.Add($name) | Out-Null
    }
    return @($names)
}

function Get-DetectorInfo {
    param([string]$Name)
    $search = Invoke-JsonRequest -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/_search" -Method Post -Body @{
        size = 100
        _source = @("name")
        query = @{ match_phrase = @{ name = $Name } }
    }

    $hit = @($search.hits.hits) | Where-Object { $_._source.name -eq $Name } | Select-Object -First 1
    if ($null -eq $hit) {
        throw "Detector '$Name' was not found."
    }

    $detectorId = [string]$hit._id
    $detail = Invoke-JsonRequest -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/$detectorId"
    $detectorType = "UNKNOWN"
    if ($detail.PSObject.Properties.Name -contains "anomaly_detector") {
        if ($detail.anomaly_detector.PSObject.Properties.Name -contains "detector_type") {
            $detectorType = [string]$detail.anomaly_detector.detector_type
        }
    }
    elseif ($detail.PSObject.Properties.Name -contains "detector_type") {
        $detectorType = [string]$detail.detector_type
    }

    $profile = Invoke-JsonRequest -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/$detectorId/_profile?_all=true"
    $state = if ($profile.PSObject.Properties.Name -contains "state") { [string]$profile.state } else { "UNKNOWN" }

    return [pscustomobject]@{
        name = $Name
        id = $detectorId
        type = $detectorType
        state = $state
    }
}

function Assert-DetectorSet {
    $expected = @(Get-ExpectedDetectorNames)
    if ($expected.Count -ne 16) {
        throw "Evaluation preflight expected 16 detector names but generated $($expected.Count)."
    }

    foreach ($name in $expected) {
        $detector = Get-DetectorInfo -Name $name
        if ($detector.type -ne "SINGLE_ENTITY") {
            throw "Detector '$name' is '$($detector.type)' instead of SINGLE_ENTITY."
        }
        if ($detector.state -ne "RUNNING") {
            throw "Detector '$name' is not RUNNING. Current state: $($detector.state)."
        }
    }
}

function Wait-ForDetectorSet {
    param([int]$TimeoutMinutes)
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $lastError = ""
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            Assert-DetectorSet
            Write-Host "All 16 OpenSearch detectors are RUNNING and SINGLE_ENTITY." -ForegroundColor Green
            return
        }
        catch {
            $lastError = $_.Exception.Message
            Write-Host "Waiting for detector preflight: $lastError"
            Start-Sleep -Seconds 30
        }
    }
    throw "Detector preflight timed out after $TimeoutMinutes minute(s). Last error: $lastError"
}

function Assert-NoActiveIncidents {
    $response = Invoke-JsonRequest -Uri "$BackendUrl/api/v1/incidents?limit=500"
    $terminal = @("RESOLVED", "CLOSED", "OPERATOR_ACTION_REQUIRED")
    $active = @($response.incidents) | Where-Object { $terminal -notcontains ([string]$_.status).ToUpperInvariant() }
    if ($active.Count -gt 0) {
        $ids = @($active | ForEach-Object { "$($_.incident_id):$($_.status)" }) -join ", "
        throw "There are non-terminal incidents before the measured run: $ids"
    }
}

function Get-MetricStats {
    param(
        [object]$Metric,
        [DateTimeOffset]$From,
        [DateTimeOffset]$To = [DateTimeOffset]::MinValue
    )
    $range = @{ gte = $From.UtcDateTime.ToString("o") }
    if ($To -ne [DateTimeOffset]::MinValue) {
        $range.lte = $To.UtcDateTime.ToString("o")
    }

    $response = Invoke-JsonRequest -Uri "$OpenSearchUrl/metrics-$($Metric.host)-*/_search?ignore_unavailable=true" -Method Post -Body @{
        size = 0
        query = @{
            bool = @{
                filter = @(
                    @{ range = @{ "@timestamp" = $range } },
                    @{ term = @{ measurement_name = [string]$Metric.measurement } },
                    @{ exists = @{ field = [string]$Metric.field } }
                )
            }
        }
        aggs = @{
            avg_value = @{ avg = @{ field = [string]$Metric.field } }
            max_value = @{ max = @{ field = [string]$Metric.field } }
        }
    }

    return [pscustomobject]@{
        average = $response.aggregations.avg_value.value
        max = $response.aggregations.max_value.value
    }
}

function Wait-ForAnomaly {
    param(
        [string]$DetectorId,
        [DateTimeOffset]$FaultStartedAt,
        [int]$TimeoutMinutes
    )
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $faultStartMs = $FaultStartedAt.ToUnixTimeMilliseconds()

    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $response = Invoke-JsonRequest -Uri "$OpenSearchUrl/_plugins/_anomaly_detection/detectors/results/_search/" -Method Post -Body @{
            size = 1
            sort = @(@{ execution_start_time = @{ order = "desc" } })
            query = @{
                bool = @{
                    filter = @(
                        @{ term = @{ detector_id = $DetectorId } },
                        @{ range = @{ anomaly_grade = @{ gt = 0 } } },
                        @{ range = @{ data_end_time = @{ gte = $faultStartMs } } }
                    )
                    must_not = @(@{ exists = @{ field = "task_id" } })
                }
            }
        }

        $hits = @($response.hits.hits)
        if ($hits.Count -gt 0) {
            return $hits[0]._source
        }
        Write-Host "Waiting for anomaly from detector $DetectorId..."
        Start-Sleep -Seconds 15
    }
    return $null
}

function Wait-ForIncident {
    param(
        [string]$DetectorId,
        [DateTimeOffset]$FaultStartedAt,
        [int]$TimeoutMinutes
    )
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $faultStartMs = $FaultStartedAt.ToUnixTimeMilliseconds()

    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $response = Invoke-JsonRequest -Uri "$BackendUrl/api/v1/incidents?limit=500"
        $matches = @($response.incidents) | Where-Object {
            $anomaly = $_.anomaly
            if ($null -eq $anomaly) { return $false }
            if ([string]$anomaly.detector_id -ne $DetectorId) { return $false }
            $execution = 0
            try { $execution = [int64]$anomaly.execution_start_time } catch { $execution = 0 }
            return $execution -ge $faultStartMs
        } | Sort-Object -Property created_at -Descending

        if ($matches.Count -gt 0) {
            return [string]$matches[0].incident_id
        }
        Write-Host "Waiting for agentic incident associated with detector $DetectorId..."
        Start-Sleep -Seconds 5
    }
    return $null
}

function Wait-ForTerminalIncident {
    param(
        [string]$IncidentId,
        [int]$TimeoutMinutes
    )
    $terminal = @("RESOLVED", "CLOSED", "OPERATOR_ACTION_REQUIRED")
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $last = $null

    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $last = Invoke-JsonRequest -Uri "$BackendUrl/api/v1/incidents/$IncidentId"
        $status = ([string]$last.status).ToUpperInvariant()
        Write-Host "Incident $IncidentId status: $status"
        if ($terminal -contains $status) {
            return $last
        }
        Start-Sleep -Seconds 5
    }
    return $last
}

function Start-ControlledFault {
    param([object]$ScenarioConfig)
    $relative = [string]$ScenarioConfig.fault.start_script
    $scriptPath = Join-Path $repoRoot $relative
    if (-not (Test-Path $scriptPath)) {
        throw "Fault start script was not found: $scriptPath"
    }
    $parameters = ConvertTo-Hashtable -Object $ScenarioConfig.fault.parameters
    & $scriptPath @parameters
    if ($LASTEXITCODE -ne 0) {
        throw "Fault start script failed: $scriptPath"
    }
}

function Stop-ControlledFault {
    param([object]$ScenarioConfig)
    $relative = [string]$ScenarioConfig.fault.stop_script
    $scriptPath = Join-Path $repoRoot $relative
    if (Test-Path $scriptPath) {
        try {
            & $scriptPath
        }
        catch {
            Write-Warning "Fault stop script failed: $($_.Exception.Message)"
        }
    }
}

function Invoke-RunScoring {
    param([string]$RunDirectory)
    & python $scoreScript --run-dir $RunDirectory --ground-truth $groundTruthPath | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Run scoring failed for $RunDirectory"
    }
}

function Invoke-OneEvaluationRun {
    param(
        [string]$ScenarioName,
        [object]$ScenarioConfig,
        [string]$ProfileName,
        [object]$ModelProfile,
        [int]$RunNumber,
        [int]$Recovery,
        [int]$DetectorWait,
        [int]$IncidentWait,
        [int]$DiagnosisWait,
        [string]$CampaignResultsRoot,
        [string]$GitSha
    )

    $runDirectory = Join-Path $CampaignResultsRoot "$ProfileName\$ScenarioName\run-$($RunNumber.ToString('00'))"
    if (Test-Path $runDirectory) {
        throw "Run directory already exists and will not be overwritten: $runDirectory"
    }
    New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null

    Write-Step "$ScenarioName / repetition $RunNumber"
    Reset-MonitoredSystem
    Restart-AgenticBackend -ModelProfile $ModelProfile
    Warm-Models -ModelProfile $ModelProfile

    if ($Recovery -gt 0) {
        Write-Host "Clean detector recovery: $Recovery minute(s)."
        Start-Sleep -Seconds ($Recovery * 60)
    }

    Assert-NoActiveIncidents

    $detector = Get-DetectorInfo -Name ([string]$ScenarioConfig.expected_detector)
    if ($detector.type -ne "SINGLE_ENTITY" -or $detector.state -ne "RUNNING") {
        throw "Expected detector '$($detector.name)' is not a RUNNING SINGLE_ENTITY detector."
    }

    $baselineStats = Get-MetricStats -Metric $ScenarioConfig.metric -From ([DateTimeOffset]::UtcNow.AddMinutes(-5))
    $faultStartedAt = [DateTimeOffset]::UtcNow
    $metadata = [ordered]@{
        campaign = $Campaign
        profile = $ProfileName
        scenario = $ScenarioName
        repetition = $RunNumber
        git_sha = $GitSha
        reasoning_model = [string]$ModelProfile.reasoning_model
        tool_model = [string]$ModelProfile.tool_model
        embedding_model = [string]$ModelProfile.embedding_model
        temperature = $ModelProfile.temperature
        expected_detector = [string]$ScenarioConfig.expected_detector
        fault_parameters = $ScenarioConfig.fault.parameters
        recovery_minutes = $Recovery
        fault_started_at_utc = $faultStartedAt.UtcDateTime.ToString("o")
        diagnosis_completed_at_utc = $null
        run_outcome = "RUNNING"
        error = $null
    }
    Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata

    $anomaly = $null
    $incident = $null
    try {
        Start-ControlledFault -ScenarioConfig $ScenarioConfig

        $anomaly = Wait-ForAnomaly -DetectorId $detector.id -FaultStartedAt $faultStartedAt -TimeoutMinutes $DetectorWait
        $faultObservedUntil = [DateTimeOffset]::UtcNow
        $faultStats = Get-MetricStats -Metric $ScenarioConfig.metric -From $faultStartedAt -To $faultObservedUntil

        if ($null -eq $anomaly) {
            $detection = [ordered]@{
                detected = $false
                detector_name = $detector.name
                detector_id = $detector.id
                detector_type = $detector.type
                anomaly_grade = $null
                confidence = $null
                anomaly_score = $null
                ttd_seconds = $null
                baseline_metric_value = $baselineStats.average
                max_metric_value = $faultStats.max
                metric_field = [string]$ScenarioConfig.metric.field
                metric_unit = [string]$ScenarioConfig.metric.unit
                failure_reason = "No anomaly_grade > 0 result was observed before the detector timeout."
            }
            Save-JsonFile -Path (Join-Path $runDirectory "detection.json") -Value $detection
            Save-JsonFile -Path (Join-Path $runDirectory "incident.json") -Value @{ found = $false; reason = "No detected anomaly, therefore no target diagnostic incident was expected." }
            $metadata.run_outcome = "DETECTOR_MISS"
            $metadata.diagnosis_completed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
            Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata
            Invoke-OneEvaluationRunCleanup -ScenarioConfig $ScenarioConfig
            Invoke-RunScoring -RunDirectory $runDirectory
            return
        }

        $executionStartMs = [int64]$anomaly.execution_start_time
        $ttdSeconds = [math]::Round(($executionStartMs - $faultStartedAt.ToUnixTimeMilliseconds()) / 1000.0, 3)
        $detection = [ordered]@{
            detected = $true
            detector_name = $detector.name
            detector_id = $detector.id
            detector_type = $detector.type
            anomaly_grade = $anomaly.anomaly_grade
            confidence = $anomaly.confidence
            anomaly_score = $anomaly.anomaly_score
            ttd_seconds = $ttdSeconds
            anomaly_execution_start_time = $anomaly.execution_start_time
            anomaly_data_start_time = $anomaly.data_start_time
            anomaly_data_end_time = $anomaly.data_end_time
            baseline_metric_value = $baselineStats.average
            max_metric_value = $faultStats.max
            metric_field = [string]$ScenarioConfig.metric.field
            metric_unit = [string]$ScenarioConfig.metric.unit
            raw_anomaly = $anomaly
            failure_reason = ""
        }
        Save-JsonFile -Path (Join-Path $runDirectory "detection.json") -Value $detection

        $incidentId = Wait-ForIncident -DetectorId $detector.id -FaultStartedAt $faultStartedAt -TimeoutMinutes $IncidentWait
        if ([string]::IsNullOrWhiteSpace($incidentId)) {
            Save-JsonFile -Path (Join-Path $runDirectory "incident.json") -Value @{ found = $false; reason = "OpenSearch detected the fault but no matching incident appeared before the timeout." }
            $metadata.run_outcome = "INCIDENT_TIMEOUT"
            $metadata.diagnosis_completed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
            Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata
            Invoke-OneEvaluationRunCleanup -ScenarioConfig $ScenarioConfig
            Invoke-RunScoring -RunDirectory $runDirectory
            throw "No agentic incident was created for detected anomaly $($detector.name). Campaign stopped to avoid contaminating later runs."
        }

        $incident = Wait-ForTerminalIncident -IncidentId $incidentId -TimeoutMinutes $DiagnosisWait
        if ($null -eq $incident) {
            throw "Incident $incidentId could not be read."
        }

        Save-JsonFile -Path (Join-Path $runDirectory "incident.json") -Value $incident
        $status = ([string]$incident.status).ToUpperInvariant()
        if (@("RESOLVED", "CLOSED", "OPERATOR_ACTION_REQUIRED") -notcontains $status) {
            $metadata.run_outcome = "DIAGNOSIS_TIMEOUT"
            $metadata.diagnosis_completed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
            Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata
            Invoke-OneEvaluationRunCleanup -ScenarioConfig $ScenarioConfig
            Invoke-RunScoring -RunDirectory $runDirectory
            throw "Incident $incidentId did not reach a terminal state before the diagnosis timeout. Campaign stopped to preserve isolation."
        }

        $finalFaultStats = Get-MetricStats -Metric $ScenarioConfig.metric -From $faultStartedAt
        $detection.max_metric_value = $finalFaultStats.max
        Save-JsonFile -Path (Join-Path $runDirectory "detection.json") -Value $detection

        $metadata.run_outcome = if ($status -eq "RESOLVED" -or $status -eq "CLOSED") { "COMPLETED" } else { $status }
        $metadata.diagnosis_completed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
        Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata
    }
    catch {
        $metadata.run_outcome = if ($metadata.run_outcome -eq "RUNNING") { "FAILED" } else { $metadata.run_outcome }
        $metadata.error = $_.Exception.Message
        $metadata.diagnosis_completed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
        Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata
        throw
    }
    finally {
        Invoke-OneEvaluationRunCleanup -ScenarioConfig $ScenarioConfig
    }

    Invoke-RunScoring -RunDirectory $runDirectory
}

function Invoke-OneEvaluationRunCleanup {
    param([object]$ScenarioConfig)
    try {
        Stop-ControlledFault -ScenarioConfig $ScenarioConfig
    }
    catch {
        Write-Warning $_.Exception.Message
    }
    try {
        Reset-MonitoredSystem
    }
    catch {
        Write-Warning "Final reset failed: $($_.Exception.Message)"
    }
}

Write-Step "Loading evaluation configuration"
Assert-Command -Name "git"
Assert-Command -Name "docker"
Assert-Command -Name "python"
$gitSha = Assert-EvaluationBranch

$groundTruth = Get-JsonFile -Path $groundTruthPath
$modelProfiles = Get-JsonFile -Path $modelProfilesPath
$campaignConfig = Get-JsonFile -Path $campaignPath

$profileName = if ([string]::IsNullOrWhiteSpace($Profile)) { [string]$campaignConfig.profile } else { $Profile }
$modelProfile = Get-NamedProperty -Object $modelProfiles.profiles -Name $profileName
Assert-ModelsAvailable -ModelProfile $modelProfile

if ($PrepareEnvironment) {
    Prepare-Environment -ModelProfile $modelProfile
}

Write-Step "Evaluation preflight"
Wait-ForHttp -Uri "$OpenSearchUrl/_cluster/health" -TimeoutSeconds 300
Wait-ForHttp -Uri "$BackendUrl/health" -TimeoutSeconds 300
Wait-ForGatewayHealthy
$preflightWait = [int]$campaignConfig.detector_preflight_wait_minutes
Wait-ForDetectorSet -TimeoutMinutes $preflightWait
Assert-NoActiveIncidents
Write-Host "Evaluation preflight passed." -ForegroundColor Green

if ($PreflightOnly) {
    Write-Host "Preflight-only execution completed. No fault was injected."
    exit 0
}

$scenarioNames = @()
if ($Scenario.Trim().ToLowerInvariant() -eq "all") {
    $scenarioNames = @($campaignConfig.scenarios | ForEach-Object { [string]$_ })
}
else {
    $scenarioNames = @($Scenario.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

foreach ($scenarioName in $scenarioNames) {
    Get-NamedProperty -Object $groundTruth.scenarios -Name $scenarioName | Out-Null
}

$runCount = if ($Repetitions -gt 0) { $Repetitions } else { [int]$campaignConfig.repetitions }
$recovery = if ($RecoveryMinutes -ge 0) { $RecoveryMinutes } else { [int]$campaignConfig.recovery_minutes }
$detectorWait = [int]$campaignConfig.detector_wait_minutes
$incidentWait = [int]$campaignConfig.incident_wait_minutes
$diagnosisWait = [int]$campaignConfig.diagnosis_wait_minutes

if ($runCount -le 0) {
    throw "Repetitions must be greater than zero."
}
if ($recovery -lt 0) {
    throw "RecoveryMinutes cannot be negative."
}

$campaignId = "$Campaign-$([DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss'))"
$campaignResultsRoot = Join-Path $resultsRoot $campaignId
New-Item -ItemType Directory -Path $campaignResultsRoot -Force | Out-Null

$campaignMetadata = [ordered]@{
    campaign_id = $campaignId
    campaign = $Campaign
    started_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
    git_sha = $gitSha
    profile = $profileName
    reasoning_model = [string]$modelProfile.reasoning_model
    tool_model = [string]$modelProfile.tool_model
    embedding_model = [string]$modelProfile.embedding_model
    temperature = $modelProfile.temperature
    scenarios = $scenarioNames
    repetitions = $runCount
    recovery_minutes = $recovery
    detector_wait_minutes = $detectorWait
    incident_wait_minutes = $incidentWait
    diagnosis_wait_minutes = $diagnosisWait
}
Save-JsonFile -Path (Join-Path $campaignResultsRoot "campaign-metadata.json") -Value $campaignMetadata

Write-Step "Starting campaign $campaignId"
Write-Host "Profile: $profileName"
Write-Host "Scenarios: $($scenarioNames -join ', ')"
Write-Host "Repetitions per scenario: $runCount"
Write-Host "Recovery before each measured fault: $recovery minute(s)"

foreach ($scenarioName in $scenarioNames) {
    $scenarioConfig = Get-NamedProperty -Object $groundTruth.scenarios -Name $scenarioName
    for ($run = 1; $run -le $runCount; $run++) {
        Invoke-OneEvaluationRun `
            -ScenarioName $scenarioName `
            -ScenarioConfig $scenarioConfig `
            -ProfileName $profileName `
            -ModelProfile $modelProfile `
            -RunNumber $run `
            -Recovery $recovery `
            -DetectorWait $detectorWait `
            -IncidentWait $incidentWait `
            -DiagnosisWait $diagnosisWait `
            -CampaignResultsRoot $campaignResultsRoot `
            -GitSha $gitSha
    }
}

Write-Step "Aggregating campaign results"
& python $aggregateScript --results-root $campaignResultsRoot | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Campaign aggregation failed."
}

$campaignMetadata.completed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
Save-JsonFile -Path (Join-Path $campaignResultsRoot "campaign-metadata.json") -Value $campaignMetadata

Write-Host ""
Write-Host "Evaluation completed." -ForegroundColor Green
Write-Host "Results: $campaignResultsRoot"
Write-Host "Summary CSV: $(Join-Path $campaignResultsRoot 'summary.csv')"
Write-Host "Model comparison CSV: $(Join-Path $campaignResultsRoot 'model-comparison.csv')"
