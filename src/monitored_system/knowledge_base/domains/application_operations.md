---
kb_id: monitored-system.domain.application-operations
version: 2
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

For one operation, the same `request_id` can appear in multiple services and provides a stronger identity relation than timestamp proximity alone.

## api-gateway behaviour

Important events are:

- `http_request_completed`: final web request status and total request latency;
- `downstream_request_completed`: status and latency of the call to `processing-service`;
- `notes_service_unavailable`: the request to `processing-service` failed before a normal response was obtained;
- `request_failed`: a web request completed with HTTP 503 because a required downstream operation was unavailable.

The gateway `/health` route calls `processing-service /health`, so its result includes downstream application state.

## processing-service behaviour

`processing-service` validates Notes payloads and forwards persistence operations to `data-service`.

Important events include:

- `downstream_request_completed`: status and latency of the call to `data-service`;
- `data_service_unavailable`: the call to `data-service` failed before a normal response was obtained;
- `notes_listed`, `note_created`, `note_updated` and `note_deleted`: successful application operations.

## data-service behaviour

`data-service` provides persistence through SQLite and exposes health and CRUD endpoints.

Important events include:

- `notes_listed`;
- `note_read`;
- `note_created`;
- `note_updated`;
- `note_deleted`;
- `note_not_found`;
- `service_started`;
- `service_stopped`.

The `/health` endpoint opens SQLite and performs a simple query.

## Application timing

Several `latency_ms` values exist at different layers:

- traffic-generator latency covers the user-visible HTTP action;
- api-gateway request latency covers the gateway's full handling of the web request;
- api-gateway downstream latency covers its call to processing-service;
- processing-service downstream latency covers its call to data-service.

These values are nested observations of different operations and are not interchangeable.

## Error propagation

An error response can move upstream through the dependency chain because callers return results from downstream services.

For example, a failed call from processing-service to data-service can result in an error returned to api-gateway, which can then be visible to traffic-generator.

This describes implemented application propagation. It does not identify the root cause of a particular live error.

## Health and availability semantics

A running process, a successful network connection, a successful local request and a healthy complete request path are different conditions.

A service may remain running while returning an error because one of its dependencies cannot complete the requested operation.

## Application knowledge boundary

This document defines service behaviour, event semantics, request correlation and timing scope. The agents must use live logs, metrics and tool observations to decide which explanation fits an active incident.
