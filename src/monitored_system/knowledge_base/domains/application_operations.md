---
kb_id: monitored-system.domain.application-operations
version: 1
domain: monitored_system
document_type: domain-reference
roles: [application_engineer, technical_lead, software_developer]
domains: [application, services, dependencies, health, logs, latency]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [application-latency, availability, downstream-failure]
source_files:
  [
    src/monitored_system/src/api-gateway/app.py,
    src/monitored_system/src/processing-service/app.py,
    src/monitored_system/src/data-service/app.py,
    src/monitored_system/src/traffic-generator/generator.py,
  ]
---

# Application Operations Reference

## Request flow

The operational request path is:

```text
traffic-generator -> api-gateway -> processing-service -> data-service
```

`api-gateway` is the web-facing entry point. `processing-service` implements the Notes application layer. `data-service` owns persistence.

## Request correlation

Requests use `X-Request-ID`. The identifier is propagated through the service chain and is emitted in application logs when available.

When an incident affects one user operation, `request_id` should be used to reconstruct the same request across services before comparing unrelated log entries.

## api-gateway operational behaviour

`api-gateway` forwards Notes operations to `processing-service`. Important operational events are:

- `http_request_completed`: final web request status and total request latency;
- `downstream_request_completed`: status and latency of the call to `processing-service`;
- `notes_service_unavailable`: the gateway could not contact `processing-service`;
- `request_failed`: a web request failed because a downstream service was unavailable.

A gateway HTTP 503 can therefore be a propagated downstream symptom.

## processing-service operational behaviour

`processing-service` validates Notes payloads and forwards persistence operations to `data-service`.

Important events include:

- `downstream_request_completed`: status and latency of the call to `data-service`;
- `data_service_unavailable`: the service could not contact `data-service`;
- note-operation events such as `notes_listed`, `note_created`, `note_updated` and `note_deleted`.

A processing-service error must be interpreted together with downstream evidence. An error emitted here does not automatically mean that processing-service is the root cause.

## data-service operational behaviour

`data-service` provides persistence through SQLite and exposes health plus CRUD endpoints.

Useful events include:

- `notes_listed`
- `note_read`
- `note_created`
- `note_updated`
- `note_deleted`
- `note_not_found`
- `service_started`
- `service_stopped`

The `/health` endpoint also checks that the SQLite database can be opened and queried.

## Latency interpretation

User-visible latency can accumulate across the request chain. To locate the likely source:

- compare traffic-generator action latency with api-gateway request latency;
- compare api-gateway downstream latency with processing-service downstream latency;
- compare application timing with network-service probe timing;
- inspect downstream status codes and service-specific errors;
- order evidence by timestamp and `request_id`.

High upstream latency is an impact signal, not sufficient evidence of an upstream root cause.

## Availability interpretation

When one downstream component becomes unavailable, upstream services can remain running and still emit errors. A useful distinction is:

```text
service process available != complete request path healthy
```

For example, api-gateway can be alive while returning 503 because processing-service or a deeper dependency cannot complete the request.

## Diagnostic principles

When investigating application operations:

- follow the real dependency direction;
- correlate the same request across services;
- separate local application errors from propagated downstream failures;
- compare application latency with network evidence;
- use health endpoints and fresh telemetry as supporting evidence;
- prefer the first component whose evidence explains the failure rather than the component with the most visible upstream error.
