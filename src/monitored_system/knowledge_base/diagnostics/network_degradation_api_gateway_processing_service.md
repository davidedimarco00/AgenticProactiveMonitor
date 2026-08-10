---
kb_id: monitored-system.diagnostic.network-degradation-api-gateway-processing-service
version: 1
domain: monitored_system
document_type: diagnostic-guide
agents: [evidence, reasoning, critic]
services: [api-gateway, processing-service]
incident_types: [network-latency, network-performance]
source_files: [src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/docker-compose.yml, src/monitored_system/src/api-gateway/app.py, src/monitored_system/src/processing-service/app.py, src/infrastructure/opensearch/init/create-anomaly-detectors.sh]
---

# Network Degradation: api-gateway to processing-service

## Observed link

The relevant application dependency is:

```text
api-gateway -> processing-service:8000
```

The corresponding OpenSearch detector is:

```text
NETLAT-api-gateway-processing-service
```

The detector is SINGLE_ENTITY and uses telemetry produced by `api-gateway` for this configured target.

Its primary feature is:

```text
measurement_name = network_service_latency
field            = network_service_latency.response_time
```

## Evidence that supports network degradation

A network-path hypothesis becomes stronger when several independent signals change together:

- `network_service_latency.response_time` increases on `api-gateway` for the processing-service target;
- `ping.average_response_ms` or ping percentiles increase for the same target;
- api-gateway downstream request latency increases;
- traffic-generator user-visible latency increases after the downstream delay appears.

`network_service_latency.result_code` can support the diagnosis when the active probe fails or does not receive the expected response.

## Evidence that weakens the hypothesis

A network-path diagnosis is weaker when:

- raw ICMP RTT remains close to baseline;
- `network_service_latency.response_time` remains close to baseline;
- only Notes request processing becomes slow;
- logs point to a local application or downstream-service problem instead.

## Alternative explanations

Similar user-visible latency can be caused by:

- slow processing inside `processing-service`;
- high CPU on `processing-service`;
- `data-service` degradation or unavailability;
- application errors that trigger slow failure paths.

The location of the first abnormal signal is important. Upstream latency alone does not prove that the network link is the root cause.

## Useful diagnostic checks

- query `network_service_latency.response_time` on the api-gateway metrics index;
- inspect `network_service_latency.result_code`;
- compare raw ping RTT and percentiles for the processing-service target;
- inspect api-gateway `downstream_request_completed` latency;
- correlate affected requests by `request_id`;
- inspect processing-service logs and downstream evidence when network signals remain normal.

## Diagnosis rule

Prefer a network diagnosis when active network probes and application timing are mutually consistent. If network probes remain normal, investigate application or downstream-service causes before concluding that the link is degraded.
