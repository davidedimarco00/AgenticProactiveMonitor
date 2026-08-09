---
kb_id: monitored-system.runbook.high-application-latency-processing-service
version: 1
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [application-latency, performance]
source_files: [src/monitored_system/infrastructure/scenarios/high-latency/scenario.yaml, src/monitored_system/src/processing-service/app.py]
---

# Runbook: High Application Latency on processing-service

## Ground-truth controlled fault

The scenario writes a runtime delay value used by `processing-service`. Before forwarding a request to data-service, the service applies a deterministic asynchronous sleep. Default injected delay is 2000 ms. The delay can be changed or removed without rebuilding the container.

## Expected evidence

Important symptoms:

- api-gateway request latency increases;
- traffic-generator user-action latency increases;
- processing-service emits `fault_delay_applied` with `fault_type=high_latency` and `delay_ms`;
- requests can become HTTP 503 when the injected delay exceeds upstream timeout budgets.

There is currently no dedicated OpenSearch anomaly detector for this application-level delay in the metric-only detector set.

## Diagnostic interpretation

This fault is internal processing delay, not packet-path delay. The most important discriminator is the relation between application timing and active network probes.

Evidence supporting application latency:

- high request `latency_ms`;
- `fault_delay_applied` warnings;
- ICMP RTT to processing-service near normal baseline;
- TCP connection time not showing a comparable increase.

If ICMP and TCP timing also rise strongly, investigate the network-latency hypothesis instead of assuming the processing delay is the only cause.

## Useful checks

- Search processing-service logs for `fault_delay_applied`.
- Compare api-gateway downstream request latency.
- Compare traffic-generator synthetic action latency.
- Query ping and TCP response-time metrics on the api-gateway -> processing-service link.

## Recovery knowledge

Remove the runtime processing-delay control file through the scenario stop mechanism.

## Validation after recovery

New requests should complete near the normal latency range, `fault_delay_applied` events should stop, and network probes should remain healthy.
