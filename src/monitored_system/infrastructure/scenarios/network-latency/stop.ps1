$ErrorActionPreference = "Stop"

$container = "api-gateway"
$destination = "processing-service"
$stateFile = "/var/run/monitored-faults/network-latency-interface"

Write-Host "Stopping scenario: network-latency"

$running = docker inspect -f "{{.State.Running}}" $container 2>$null
if ($running -ne "true") {
    Write-Host "api-gateway is not running. Nothing to remove."
    exit 0
}

$interface = (docker exec $container sh -c "cat $stateFile 2>/dev/null || true" | Out-String).Trim()

if ([string]::IsNullOrWhiteSpace($interface)) {
    $destinationIp = (docker exec $container sh -c "getent ahostsv4 $destination | sed -n '1{s/[[:space:]].*//;p;}'" | Out-String).Trim()
    if (-not [string]::IsNullOrWhiteSpace($destinationIp)) {
        $route = (docker exec $container sh -c "ip route get $destinationIp" | Out-String).Trim()
        if ($route -match '\bdev\s+([^\s]+)') {
            $interface = $Matches[1]
        }
    }
}

if ([string]::IsNullOrWhiteSpace($interface)) {
    Write-Host "Unable to determine the application interface. No qdisc was removed."
    exit 0
}

$currentQdisc = (docker exec $container sh -c "tc qdisc show dev $interface" | Out-String).Trim()
if ($currentQdisc -match '\bnetem\b') {
    docker exec $container sh -c "tc qdisc del dev $interface root"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to remove tc/netem from $container/$interface."
    }
    Write-Host "Removed tc/netem from $container/$interface."
}
else {
    Write-Host "No active netem qdisc found on $container/$interface."
}

docker exec $container sh -c "rm -f $stateFile" 2>$null | Out-Null
$global:LASTEXITCODE = 0

Write-Host "Scenario stopped. Network latency should return to the normal baseline."
