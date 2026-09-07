param(
    [string]$Campaign = "baseline",
    [string]$Scenario = "all",
    [int]$Repetitions = 0,
    [int]$RecoverySeconds = -1,
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
        [object]$Body = $null,
        [int]$TimeoutSeconds = 60
    )

    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -TimeoutSec $TimeoutSeconds
    }

    $json = $Body | ConvertTo-Json -Depth 100 -Compress
    return Invoke-RestMethod `
        -Uri $Uri `
        -Method $Method `
        -ContentType "application/json" `
        -Body $json `
        -TimeoutSec $TimeoutSeconds
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
            $response = Invoke-WebRequest `
                -Uri "$GatewayUrl/health" `
                -Method Get `
                -UseBasicParsing `
                -TimeoutSec 10
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
        $name = [string]$model.name
        if (
            [string]::IsNullOrWhiteSpace($name) -and
            $model.PSObject.Properties.Name -contains "model"
        ) {
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
            throw (
                "Required Ollama model '$model' is not installed. " +
                "No fallback is allowed during evaluation. Installed models: " +
                ($installed -join ", ")
            )
        }
    }
}

function Warm-Models {
    param([object]$ModelProfile)

    if ($SkipModelWarmup) {
        return
    }

    Write-Step "Warming Ollama models"
    foreach (
        $model in @(
            [string]$ModelProfile.reasoning_model,
            [string]$ModelProfile.tool_model
        ) | Select-Object -Unique
    ) {
        Invoke-JsonRequest `
            -Uri "$OllamaUrl/api/chat" `
            -Method Post `
            -Body @{
                model = $model
                messages = @(
                    @{
                        role = "user"
                        content = "Evaluation warm-up. Reply with OK."
                    }
                )
                stream = $false
                options = @{ temperature = 0 }
            } `
            -TimeoutSeconds 180 | Out-Null
        Write-Host "Warmed model: $model"
    }

    Invoke-JsonRequest `
        -Uri "$OllamaUrl/api/embed" `
        -Method Post `
        -Body @{
            model = [string]$ModelProfile.embedding_model
            input = @("evaluation warm-up")
        } `
        -TimeoutSeconds 180 | Out-Null
    Write-Host "Warmed embedding model: $($ModelProfile.embedding_model)"
}

function Set-EvaluationEnvironment {
    param([object]$ModelProfile)

    $env:OLLAMA_CHAT_MODEL = [string]$ModelProfile.reasoning_model
    $env:OLLAMA_TOOL_MODEL = [string]$ModelProfile.tool_model
    $env:OLLAMA_EMBEDDING_MODEL = [string]$ModelProfile.embedding_model

    # Evaluation isolates diagnosis from detector quality.
    $env:ENABLE_TEST_ANOMALY_INJECTION = "1"
    $env:ENABLE_OPENSEARCH_ANOMALY_WATCHER = "0"
}

function Restart-AgenticBackend {
    param(
        [object]$ModelProfile,
        [switch]$Build
    )

    Set-EvaluationEnvironment -ModelProfile $ModelProfile
    Push-Location $infrastructureRoot
    try {
        $composeArgs = @(
            "compose",
            "-f", "docker-compose.yml",
            "-f", "docker-compose.test.yml",
            "up", "-d",
            "--force-recreate"
        )
        if ($Build) {
            $composeArgs += "--build"
        }
        $composeArgs += "agentic-backend"

        & docker @composeArgs
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose could not start the evaluation agentic backend."
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
    Wait-ForGatewayHealthy
}

function Prepare-Environment {
    param([object]$ModelProfile)

    Write-Step "Preparing diagnostic evaluation environment"
    Restart-AgenticBackend -ModelProfile $ModelProfile -Build
    Reset-MonitoredSystem
}

function Assert-TestInjectionRoute {
    $openApi = Invoke-JsonRequest -Uri "$BackendUrl/openapi.json"
    $pathNames = @($openApi.paths.PSObject.Properties.Name)
    if ($pathNames -notcontains "/internal/v1/test/anomalies") {
        throw (
            "Synthetic anomaly injection route is not mounted. " +
            "The evaluation backend must run with ENABLE_TEST_ANOMALY_INJECTION=1."
        )
    }
}

function Assert-NoActiveIncidents {
    $response = Invoke-JsonRequest -Uri "$BackendUrl/api/v1/incidents?limit=500"
    $terminal = @("RESOLVED", "CLOSED", "OPERATOR_ACTION_REQUIRED")
    $active = @(
        @($response.incidents) |
            Where-Object {
                $terminal -notcontains ([string]$_.status).ToUpperInvariant()
            }
    )

    if ($active.Count -gt 0) {
        $ids = @(
            $active |
                ForEach-Object { "$($_.incident_id):$($_.status)" }
        ) -join ", "
        throw "There are non-terminal incidents before the measured run: $ids"
    }
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
}

function Stop-ControlledFault {
    param([object]$ScenarioConfig)

    $relative = [string]$ScenarioConfig.fault.stop_script
    $scriptPath = Join-Path $repoRoot $relative
    if (-not (Test-Path $scriptPath)) {
        return
    }

    try {
        & $scriptPath
    }
    catch {
        Write-Warning "Fault stop script failed: $($_.Exception.Message)"
    }
}

function Invoke-SyntheticAnomaly {
    param(
        [string]$ScenarioName,
        [object]$ScenarioConfig,
        [string]$RunToken
    )

    $synthetic = $ScenarioConfig.synthetic_anomaly
    $detectorId = "evaluation-$ScenarioName-$RunToken"
    $resultId = "evaluation-result-$ScenarioName-$RunToken"

    $body = @{
        detector_id = $detectorId
        detector_name = [string]$synthetic.detector_name
        detector_description = [string]$synthetic.detector_description
        anomaly_grade = [double]$synthetic.anomaly_grade
        confidence = [double]$synthetic.confidence
        anomaly_score = [double]$synthetic.anomaly_score
        result_id = $resultId
    }

    $response = Invoke-JsonRequest `
        -Uri "$BackendUrl/internal/v1/test/anomalies" `
        -Method Post `
        -Body $body `
        -TimeoutSeconds 60

    return [pscustomobject]@{
        detector_id = $detectorId
        result_id = $resultId
        request = $body
        response = $response
    }
}

function Wait-ForIncident {
    param(
        [string]$DetectorId,
        [int]$TimeoutMinutes
    )

    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $response = Invoke-JsonRequest -Uri "$BackendUrl/api/v1/incidents?limit=500"
        $matches = @(
            @($response.incidents) |
                Where-Object {
                    $anomaly = $_.anomaly
                    $null -ne $anomaly -and
                    [string]$anomaly.detector_id -eq $DetectorId
                } |
                Sort-Object -Property created_at -Descending
        )

        if ($matches.Count -gt 0) {
            return [string]$matches[0].incident_id
        }

        Write-Host "Waiting for synthetic diagnostic incident..."
        Start-Sleep -Seconds 2
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
        Start-Sleep -Seconds 3
    }

    return $last
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
        Write-Warning "Final monitored-system reset failed: $($_.Exception.Message)"
    }
}

function Invoke-RunScoring {
    param([string]$RunDirectory)

    & python $scoreScript `
        --run-dir $RunDirectory `
        --ground-truth $groundTruthPath | Out-Host

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
        [int]$IncidentWait,
        [int]$DiagnosisWait,
        [string]$CampaignResultsRoot,
        [string]$GitSha
    )

    $runDirectory = Join-Path `
        $CampaignResultsRoot `
        "$ProfileName\$ScenarioName\run-$($RunNumber.ToString('00'))"

    if (Test-Path $runDirectory) {
        throw "Run directory already exists and will not be overwritten: $runDirectory"
    }
    New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null

    Write-Step "$ScenarioName / repetition $RunNumber"

    $metadata = [ordered]@{
        campaign = $Campaign
        evaluation_scope = "agentic_diagnosis_only"
        anomaly_trigger_mode = "synthetic"
        opensearch_detector_evaluated = $false
        profile = $ProfileName
        scenario = $ScenarioName
        repetition = $RunNumber
        git_sha = $GitSha
        reasoning_model = [string]$ModelProfile.reasoning_model
        tool_model = [string]$ModelProfile.tool_model
        embedding_model = [string]$ModelProfile.embedding_model
        temperature = $ModelProfile.temperature
        fault_parameters = $ScenarioConfig.fault.parameters
        fault_stabilization_seconds = [int]$ScenarioConfig.fault_stabilization_seconds
        recovery_seconds = $Recovery
        fault_started_at_utc = $null
        synthetic_triggered_at_utc = $null
        incident_created_at_utc = $null
        diagnosis_completed_at_utc = $null
        run_outcome = "RUNNING"
        error = $null
    }
    Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata

    try {
        Reset-MonitoredSystem
        Restart-AgenticBackend -ModelProfile $ModelProfile
        Warm-Models -ModelProfile $ModelProfile

        if ($Recovery -gt 0) {
            Write-Host "Clean runtime recovery: $Recovery second(s)."
            Start-Sleep -Seconds $Recovery
        }

        Assert-TestInjectionRoute
        Assert-NoActiveIncidents

        $metadata.fault_started_at_utc = (
            [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
        )
        Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata

        Start-ControlledFault -ScenarioConfig $ScenarioConfig

        $stabilization = [int]$ScenarioConfig.fault_stabilization_seconds
        if ($stabilization -gt 0) {
            Write-Host (
                "Fault is real. Waiting $stabilization second(s) before " +
                "injecting the synthetic anomaly trigger..."
            )
            Start-Sleep -Seconds $stabilization
        }

        $runToken = [guid]::NewGuid().ToString("N")
        $triggeredAt = [DateTimeOffset]::UtcNow
        $triggerResult = Invoke-SyntheticAnomaly `
            -ScenarioName $ScenarioName `
            -ScenarioConfig $ScenarioConfig `
            -RunToken $runToken

        $metadata.synthetic_triggered_at_utc = (
            $triggeredAt.UtcDateTime.ToString("o")
        )
        Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata

        Save-JsonFile `
            -Path (Join-Path $runDirectory "trigger.json") `
            -Value ([ordered]@{
                accepted = $true
                accepted_at_utc = $triggeredAt.UtcDateTime.ToString("o")
                detector_id = $triggerResult.detector_id
                result_id = $triggerResult.result_id
                request = $triggerResult.request
                response = $triggerResult.response
            })

        Write-Host (
            "Synthetic SINGLE_ENTITY anomaly queued: " +
            "$($triggerResult.request.detector_name)"
        )

        $incidentId = Wait-ForIncident `
            -DetectorId $triggerResult.detector_id `
            -TimeoutMinutes $IncidentWait

        if ([string]::IsNullOrWhiteSpace($incidentId)) {
            Save-JsonFile `
                -Path (Join-Path $runDirectory "incident.json") `
                -Value @{
                    found = $false
                    reason = "Synthetic trigger was accepted but no incident appeared before timeout."
                }
            $metadata.run_outcome = "INCIDENT_TIMEOUT"
        }
        else {
            $firstIncident = Invoke-JsonRequest `
                -Uri "$BackendUrl/api/v1/incidents/$incidentId"

            $createdAt = [string]$firstIncident.created_at
            if ([string]::IsNullOrWhiteSpace($createdAt)) {
                $createdAt = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
            }
            $metadata.incident_created_at_utc = $createdAt
            Save-JsonFile `
                -Path (Join-Path $runDirectory "metadata.json") `
                -Value $metadata

            $incident = Wait-ForTerminalIncident `
                -IncidentId $incidentId `
                -TimeoutMinutes $DiagnosisWait

            if ($null -eq $incident) {
                Save-JsonFile `
                    -Path (Join-Path $runDirectory "incident.json") `
                    -Value @{
                        found = $false
                        incident_id = $incidentId
                        reason = "Incident could not be read after creation."
                    }
                $metadata.run_outcome = "INCIDENT_READ_FAILED"
            }
            else {
                Save-JsonFile `
                    -Path (Join-Path $runDirectory "incident.json") `
                    -Value $incident

                $status = ([string]$incident.status).ToUpperInvariant()
                if ($status -in @("RESOLVED", "CLOSED")) {
                    $metadata.run_outcome = "COMPLETED"
                }
                elseif ($status -eq "OPERATOR_ACTION_REQUIRED") {
                    $metadata.run_outcome = "OPERATOR_ACTION_REQUIRED"
                }
                else {
                    $metadata.run_outcome = "DIAGNOSIS_TIMEOUT"
                }
            }
        }
    }
    catch {
        $metadata.run_outcome = "FAILED"
        $metadata.error = $_.Exception.Message

        $triggerPath = Join-Path $runDirectory "trigger.json"
        if (-not (Test-Path $triggerPath)) {
            Save-JsonFile `
                -Path $triggerPath `
                -Value @{
                    accepted = $false
                    error = $_.Exception.Message
                }
        }

        $incidentPath = Join-Path $runDirectory "incident.json"
        if (-not (Test-Path $incidentPath)) {
            Save-JsonFile `
                -Path $incidentPath `
                -Value @{
                    found = $false
                    reason = $_.Exception.Message
                }
        }

        Write-Warning "Run failed and was recorded: $($_.Exception.Message)"
    }
    finally {
        $metadata.diagnosis_completed_at_utc = (
            [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
        )
        Save-JsonFile -Path (Join-Path $runDirectory "metadata.json") -Value $metadata
        Invoke-OneEvaluationRunCleanup -ScenarioConfig $ScenarioConfig
    }

    Invoke-RunScoring -RunDirectory $runDirectory
}

Write-Step "Loading evaluation configuration"
Assert-Command -Name "git"
Assert-Command -Name "docker"
Assert-Command -Name "python"

$gitSha = Assert-EvaluationBranch
$groundTruth = Get-JsonFile -Path $groundTruthPath
$modelProfiles = Get-JsonFile -Path $modelProfilesPath
$campaignConfig = Get-JsonFile -Path $campaignPath

$profileName = if ([string]::IsNullOrWhiteSpace($Profile)) {
    [string]$campaignConfig.profile
}
else {
    $Profile
}
$modelProfile = Get-NamedProperty `
    -Object $modelProfiles.profiles `
    -Name $profileName

Assert-ModelsAvailable -ModelProfile $modelProfile

if ($PrepareEnvironment) {
    Prepare-Environment -ModelProfile $modelProfile
}

Write-Step "Diagnostic evaluation preflight"
Wait-ForHttp -Uri "$OpenSearchUrl/_cluster/health" -TimeoutSeconds 300
Wait-ForHttp -Uri "$BackendUrl/health" -TimeoutSeconds 300
Wait-ForGatewayHealthy
Assert-TestInjectionRoute
Assert-NoActiveIncidents

Write-Host (
    "Preflight passed. Real OpenSearch anomaly detection is not part of " +
    "this campaign; the OpenSearch watcher is disabled for the evaluation backend."
) -ForegroundColor Green

if ($PreflightOnly) {
    Write-Host "Preflight-only execution completed. No fault or synthetic anomaly was injected."
    exit 0
}

$scenarioNames = @()
if ($Scenario.Trim().ToLowerInvariant() -eq "all") {
    $scenarioNames = @(
        $campaignConfig.scenarios |
            ForEach-Object { [string]$_ }
    )
}
else {
    $scenarioNames = @(
        $Scenario.Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
}

foreach ($scenarioName in $scenarioNames) {
    Get-NamedProperty `
        -Object $groundTruth.scenarios `
        -Name $scenarioName | Out-Null
}

$runCount = if ($Repetitions -gt 0) {
    $Repetitions
}
else {
    [int]$campaignConfig.repetitions
}

$recovery = if ($RecoverySeconds -ge 0) {
    $RecoverySeconds
}
else {
    [int]$campaignConfig.recovery_seconds
}

$incidentWait = [int]$campaignConfig.incident_wait_minutes
$diagnosisWait = [int]$campaignConfig.diagnosis_wait_minutes

if ($runCount -le 0) {
    throw "Repetitions must be greater than zero."
}
if ($recovery -lt 0) {
    throw "RecoverySeconds cannot be negative."
}

$campaignId = "$Campaign-$([DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss'))"
$campaignResultsRoot = Join-Path $resultsRoot $campaignId
New-Item -ItemType Directory -Path $campaignResultsRoot -Force | Out-Null

$campaignMetadata = [ordered]@{
    campaign_id = $campaignId
    campaign = $Campaign
    evaluation_scope = "agentic_diagnosis_only"
    anomaly_trigger_mode = "synthetic"
    opensearch_anomaly_watcher_enabled = $false
    opensearch_detector_quality_evaluated = $false
    started_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
    git_sha = $gitSha
    profile = $profileName
    reasoning_model = [string]$modelProfile.reasoning_model
    tool_model = [string]$modelProfile.tool_model
    embedding_model = [string]$modelProfile.embedding_model
    temperature = $modelProfile.temperature
    scenarios = $scenarioNames
    repetitions = $runCount
    recovery_seconds = $recovery
    incident_wait_minutes = $incidentWait
    diagnosis_wait_minutes = $diagnosisWait
}
Save-JsonFile `
    -Path (Join-Path $campaignResultsRoot "campaign-metadata.json") `
    -Value $campaignMetadata

Write-Step "Starting diagnostic campaign $campaignId"
Write-Host "Profile: $profileName"
Write-Host "Scenarios: $($scenarioNames -join ', ')"
Write-Host "Repetitions per scenario: $runCount"
Write-Host "Runtime recovery before each fault: $recovery second(s)"
Write-Host "Faults are REAL; anomaly triggers are SYNTHETIC."

foreach ($scenarioName in $scenarioNames) {
    $scenarioConfig = Get-NamedProperty `
        -Object $groundTruth.scenarios `
        -Name $scenarioName

    for ($run = 1; $run -le $runCount; $run++) {
        Invoke-OneEvaluationRun `
            -ScenarioName $scenarioName `
            -ScenarioConfig $scenarioConfig `
            -ProfileName $profileName `
            -ModelProfile $modelProfile `
            -RunNumber $run `
            -Recovery $recovery `
            -IncidentWait $incidentWait `
            -DiagnosisWait $diagnosisWait `
            -CampaignResultsRoot $campaignResultsRoot `
            -GitSha $gitSha
    }
}

Write-Step "Aggregating diagnostic results"
& python $aggregateScript --results-root $campaignResultsRoot | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Campaign aggregation failed."
}

$campaignMetadata.completed_at_utc = (
    [DateTimeOffset]::UtcNow.UtcDateTime.ToString("o")
)
Save-JsonFile `
    -Path (Join-Path $campaignResultsRoot "campaign-metadata.json") `
    -Value $campaignMetadata

Write-Host ""
Write-Host "Diagnostic evaluation completed." -ForegroundColor Green
Write-Host "Results: $campaignResultsRoot"
Write-Host "Summary CSV: $(Join-Path $campaignResultsRoot 'summary.csv')"
Write-Host "Model comparison CSV: $(Join-Path $campaignResultsRoot 'model-comparison.csv')"
