---
kb_id: monitored-system.diagnostic.data-service-unavailability
version: 2
domain: monitored_system
document_type: diagnostic-guide
roles: [application_engineer, software_developer, network_engineer, system_engineer, technical_lead]
domains: [availability, dependencies, network, runtime, software]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [availability, downstream-failure]
source_files: [src/monitored_system/src/data-service/app.py, src/monitored_system/src/processing-service/app.py, src/monitored_system/src/api-gateway/app.py, src/monitored_system/src/traffic-generator/generator.py, src/monitored_system/docker-compose.yml]
---

# data-service Unavailability

## Dependency context

`data-service` is the persistence dependency of `processing-service`.

The relevant request chain is:

```text
api-gateway -> processing-service -> data-service
```

A data-service availability problem can therefore generate errors in multiple upstream services.

## Evidence that supports data-service unavailability

A data-service unavailability hypothesis becomes stronger when several of the following occur in the same time window:

- processing-service logs `data_service_unavailable`;
- connection or probe evidence shows that data-service cannot be reached or does not return the expected response;
- fresh data-service application telemetry or heartbeats disappear;
- api-gateway receives propagated downstream failures and returns HTTP 503;
- traffic-generator records failed user actions after the downstream failure begins.

Missing telemetry alone is not sufficient proof because an observability problem can produce a similar symptom.

## Causal interpretation

Errors observed at api-gateway or traffic-generator can be consequences of the downstream failure. The root cause should not be assigned to an upstream component only because it emits the most visible error.

When possible, order evidence by timestamp and use `request_id` to follow the same request across services.

## Alternative explanations

Similar symptoms can be caused by:

- network degradation between processing-service and data-service;
- processing-service failure before the downstream call;
- an observability-path problem that hides data-service telemetry without stopping the service;
- application errors that return failures while the service remains reachable;
- SQLite/database access failure while the data-service process remains running.

## Useful diagnostic checks

- inspect processing-service logs for `data_service_unavailable`;
- inspect `processing-service -> data-service` network-service probe timing and result code;
- verify whether fresh data-service metrics, application logs and heartbeats are present;
- inspect the data-service health result when the service remains reachable;
- correlate api-gateway 5xx responses with downstream errors by time and `request_id`;
- confirm runtime service availability when telemetry is missing.

## Diagnosis rule

Prefer a data-service unavailability diagnosis when direct downstream failure evidence and missing or failed data-service activity agree. Treat upstream 503 responses as propagated symptoms unless independent evidence identifies an upstream root cause.
