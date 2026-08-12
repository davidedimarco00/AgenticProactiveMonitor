---
kb_id: monitored-system.runbook.network-latency-api-gateway-processing-service
version: 2
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [api-gateway, processing-service]
incident_types: [network-latency, network-performance]
source_files: [src/monitored_system/infrastructure/scenarios/network-latency/scenario.yaml, src/monitored_system/infrastructure/scenarios/network-latency/start.ps1, src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/docker-compose.yml, src/infrastructure/opensearch/init/create-anomaly-detectors.sh]
---

# Runbook: Network Latency from api-gateway to processing-service

## Ground-truth controlled fault

The scenario resolves the Docker route from `api-gateway` to `processing-service` and applies Linux `tc/netem` delay to the selected application-network egress interface. Default delay is 250 ms and optional jitter can also be configured. The fault changes the packet path and does not modify application code.

The scenario requires `NET_ADMIN` on `api-gateway`. In the validated Docker Desktop for Windows thesis environment, the network fault requires the Hyper-V/LinuxKit backend with `sch_netem` support.

## Expected detector

```text
NETLAT-api-gateway-processing-service
```

The detector is SINGLE_ENTITY and reads only the `api-gateway` metrics index. Its primary feature is:

```text
measurement_name = network_service_latency
field            = network_service_latency.response_time
```

Do not treat `ping.average_response_ms` or the old `net_response.response_time` field as the detector feature.

## Expected evidence

Primary detector evidence on `api-gateway` with target `processing-service`:

- `network_service_latency.response_time` increases.

Independent supporting evidence:

- `ping.average_response_ms` increases;
- `ping.percentile95_ms` increases;
- application request latency increases at api-gateway and traffic-generator;
- interface counters remain available for packets, bytes, errors and drops.

The configured network-service probe uses TCP port 8000 and sends `GET /docs`, waiting for `200 OK`. This provides a stable service-level network probe that is independent from the Notes forwarding logic.

## Diagnostic interpretation

A simultaneous rise in `network_service_latency.response_time`, raw ICMP RTT and user-visible request latency strongly supports packet-path degradation on this link.

This is different from the controlled high-application-latency scenario. That scenario adds a sleep inside the processing-service Notes forwarding path. In that case Notes request duration and `fault_delay_applied` increase, while raw ICMP RTT and the `/docs` network-service probe should remain near baseline.

## Useful checks

- Query `network_service_latency.response_time` on the api-gateway index.
- Inspect `network_service_latency.result_code` for probe failures.
- Query raw ping RTT and percentiles on api-gateway.
- Inspect api-gateway downstream request `latency_ms`.
- Compare traffic-generator action latency.
- Check interface errors or drops if the symptoms suggest packet problems beyond pure delay.
- If the controlled scenario itself cannot start, verify `NET_ADMIN` and kernel `sch_netem` support before interpreting the absence of an anomaly.

## Recovery knowledge

Remove the root `netem` qdisc from the recorded api-gateway application interface through the scenario stop mechanism. The stop script uses the saved interface when available and removes the scenario state after cleanup.

## Validation after recovery

`network_service_latency.response_time` and raw ICMP timing should return towards baseline, followed by lower application request latency. OpenSearch telemetry should continue normally throughout the test.
