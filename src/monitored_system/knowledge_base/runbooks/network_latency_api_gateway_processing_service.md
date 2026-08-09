---
kb_id: monitored-system.runbook.network-latency-api-gateway-processing-service
version: 1
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [api-gateway, processing-service]
incident_types: [network-latency, network-performance]
source_files: [src/monitored_system/infrastructure/scenarios/network-latency/scenario.yaml, src/monitored_system/infrastructure/telegraf.conf]
---

# Runbook: Network Latency from api-gateway to processing-service

## Ground-truth controlled fault

The scenario applies Linux `tc/netem` delay to the api-gateway application-network egress interface selected by the route to `processing-service`. Default delay is 250 ms. The observability route is intentionally not the selected application interface.

## Expected evidence

Primary network signals on `api-gateway` with target `processing-service`:

- `ping.average_response_ms` increases;
- `ping.percentile95_ms` increases;
- `net_response.response_time` increases;
- application request latency increases at api-gateway and traffic-generator.

Expected detector:

```text
NETLAT-api-gateway-processing-service
```

This detector is SINGLE_ENTITY and is associated with the source/link represented by the api-gateway metrics index.

## Diagnostic interpretation

A simultaneous rise in packet-level RTT, TCP connection time and user-visible request latency strongly supports network degradation on this link.

This is different from the high-application-latency scenario, where processing-service sleeps before downstream calls. In that case application duration increases but ICMP RTT should not show the same network delay pattern.

## Useful checks

- Query ping RTT and percentiles on api-gateway.
- Query `net_response.response_time` for the processing-service target.
- Inspect api-gateway downstream request `latency_ms`.
- Compare traffic-generator action latency.
- Check interface errors or drops if the symptoms suggest packet problems beyond pure delay.

## Recovery knowledge

Remove the root netem qdisc from the recorded api-gateway application interface through the scenario stop mechanism.

## Validation after recovery

ICMP and TCP timing should return towards baseline, followed by lower application request latency. OpenSearch telemetry should continue normally throughout the test.
