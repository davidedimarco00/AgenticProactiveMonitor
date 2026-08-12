---
kb_id: monitored-system.runbook.high-application-latency-processing-service
version: 2
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [application-latency, performance]
source_files: [src/monitored_system/infrastructure/scenarios/high-latency/scenario.yaml, src/monitored_system/src/processing-service/app.py, src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/docker-compose.yml]
---

# Runbook: High Application Latency on processing-service

## Ground-truth controlled fault

The scenario writes a runtime delay value used by `processing-service`. Before forwarding a Notes request to data-service, the service applies a deterministic asynchronous sleep. Default injected delay is 2000 ms. The delay can be changed or removed without rebuilding the container.

## Expected evidence

Important symptoms:

- api-gateway downstream/request latency increases;
- traffic-generator user-action latency increases;
- processing-service emits `fault_delay_applied` with `fault_type=high_latency` and `delay_ms`;
- requests can become HTTP 503 when the injected delay exceeds upstream timeout budgets.

There is currently no dedicated OpenSearch anomaly detector for this application-level delay in the metric-only detector set.

## Diagnostic interpretation

This fault is internal processing delay, not packet-path delay. The most important discriminator is the relation between application timing and the independent network probes.

Evidence supporting application latency:

- high application `latency_ms` on the Notes request path;
- `fault_delay_applied` warnings on processing-service;
- raw `ping.average_response_ms` to processing-service remains near baseline;
- `network_service_latency.response_time` on the api-gateway -> processing-service link remains near baseline.

The `network_service_latency` probe for this link sends `GET /docs` to processing-service. This endpoint does not execute the delayed Notes forwarding path, so the controlled application sleep should not create a comparable increase in this network-service probe.

If both raw ICMP RTT and `network_service_latency.response_time` rise strongly, investigate the network-latency hypothesis instead of assuming the processing delay is the only cause.

## Useful checks

- Search processing-service logs for `fault_delay_applied`.
- Compare api-gateway downstream request latency.
- Compare traffic-generator synthetic action latency.
- Query `network_service_latency.response_time` on api-gateway.
- Query raw ping RTT on api-gateway for target processing-service.

## Recovery knowledge

Remove the runtime processing-delay control file through the scenario stop mechanism.

## Validation after recovery

New Notes requests should complete near the normal latency range, `fault_delay_applied` events should stop, and both independent network probes should remain healthy.
