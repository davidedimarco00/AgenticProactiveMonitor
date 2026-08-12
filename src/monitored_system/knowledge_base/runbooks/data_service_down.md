---
kb_id: monitored-system.runbook.data-service-down
version: 2
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [availability, downstream-failure]
source_files: [src/monitored_system/infrastructure/scenarios/data-service-down/scenario.yaml, src/monitored_system/src/processing-service/app.py, src/monitored_system/src/api-gateway/app.py, src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/docker-compose.yml]
---

# Runbook: data-service Unavailable

## Ground-truth controlled fault

The scenario stops the `data-service` container while the rest of the Notes Platform continues to receive synthetic user traffic.

## Expected evidence

Expected propagation path:

```text
data-service stopped
  -> processing-service cannot connect
  -> data_service_unavailable errors
  -> api-gateway receives downstream failure / returns 503
  -> traffic-generator records failed actions
```

Fresh data-service application telemetry and system heartbeats disappear while the container is stopped.

The `processing-service` Telegraf process remains active and continues probing `data-service:8000` with the `network_service_latency` measurement. A failed `network_service_latency.result_code` is therefore useful source-side evidence of the downstream outage.

## Diagnostic interpretation

The upstream errors are consequences of downstream unavailability. Do not diagnose api-gateway or processing-service as the root cause only because they emit errors.

Strong evidence includes:

- `data_service_unavailable` on processing-service;
- connection error type or failed `network_service_latency` probe from processing-service to data-service;
- missing fresh data-service telemetry;
- runtime confirmation that the data-service container is stopped;
- 503 responses and synthetic user failures occurring after the downstream outage.

Missing telemetry alone is not enough because collection issues can also remove data. Confirm service state where possible.

## Useful checks

- Inspect data-service container state.
- Query processing-service logs for `data_service_unavailable`.
- Query `network_service_latency.result_code` and `network_service_latency.response_time` from processing-service for target data-service.
- Inspect api-gateway status codes and traffic-generator failures.

## Recovery knowledge

Start the `data-service` container. The SQLite database is stored on the persistent `notes-data` volume and should not be deleted as part of normal recovery.

## Validation after recovery

Verify fresh data-service telemetry, a successful processing-service -> data-service network-service probe, successful health checks, successful downstream requests from processing-service, disappearance of propagated 503 errors and recovery of normal synthetic user actions.
