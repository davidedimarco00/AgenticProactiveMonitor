param(
    [ValidateRange(1, 2000)]
    [int]$DelayMs = 250,

    [ValidateRange(0, 1000)]
    [int]$JitterMs = 0
)

$ErrorActionPreference = "Stop"

$container = "api-gateway"
$destination = "processing-service"
$stateFile = "/var/run/monitored-faults/network-latency-interface"

Write-Host "Starting scenario: network-latency"
Write-Host "Source: $container"
Write-Host "Destination: $destination"
Write-Host "Injected egress delay: $DelayMs ms"
if ($JitterMs -gt 0) {
    Write-Host "Injected jitter: +/- $JitterMs ms"
}

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    throw "Container '$container' is not running. Start the monitored system first."
}

$destinationIp = (docker exec $container sh -c "getent ahostsv4 $destination | sed -n '1{s/[[:space:]].*//;p;}'" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($destinationIp)) {
    throw "Unable to resolve '$destination' from '$container'."
}

$route = (docker exec $container sh -c "ip route get $destinationIp" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $route -notmatch '\bdev\s+([^\s]+)') {
    throw "Unable to determine the application network interface for $destinationIp."
}

$interface = $Matches[1]
Write-Host "Application route: $destinationIp via $interface"

$currentQdisc = (docker exec $container sh -c "tc qdisc show dev $interface" | Out-String).Trim()
if ($currentQdisc -match '\bnetem\b') {
    throw "A netem qdisc is already active on $container/$interface. Run stop.ps1 first."
}

if ($JitterMs -gt 0) {
    $tcCommand = "tc qdisc replace dev $interface root netem delay ${DelayMs}ms ${JitterMs}ms"
}
else {
    $tcCommand = "tc qdisc replace dev $interface root netem delay ${DelayMs}ms"
}

docker exec $container sh -c $tcCommand
if ($LASTEXITCODE -ne 0) {
    throw "Unable to apply tc/netem. Verify that api-gateway has NET_ADMIN capability and that Docker uses a kernel with sch_netem support."
}

docker exec $container sh -c "mkdir -p /var/run/monitored-faults && printf '%s\n' '$interface' > $stateFile"
if ($LASTEXITCODE -ne 0) {
    throw "tc/netem was applied, but the scenario state could not be saved."
}

$verification = (docker exec $container sh -c "tc qdisc show dev $interface" | Out-String).Trim()
if ($verification -notmatch '\bnetem\b') {
    throw "tc/netem did not remain active on $container/$interface."
}

Write-Host "Scenario active: $verification"
Write-Host "Telegraf ping RTT and network_service_latency.response_time should increase for api-gateway -> processing-service."
Write-Host "Run .\infrastructure\scenarios\network-latency\stop.ps1 to restore the normal path."
