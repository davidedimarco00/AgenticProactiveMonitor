---
kb_id: monitored-system.service.processing-service
version: 2
domain: monitored_system
document_type: service-reference
roles:
  [
    technical_lead,
    system_engineer,
    network_engineer,
    application_engineer,
    software_developer,
  ]
domains:
  [
    application,
    api,
    validation,
    downstream,
    latency,
    error-propagation,
    request-correlation,
  ]
services: [api-gateway, processing-service, data-service]
incident_types:
  [
    cpu,
    application-latency,
    availability,
    downstream-failure,
    network-latency,
    memory,
  ]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/src/processing-service/app.py,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# processing-service Service

## Purpose

`processing-service` is the FastAPI application layer of the Notes Platform. It receives note operations from `api-gateway`, validates note payloads and forwards persistence operations to `data-service`.

Its direct dependency is:

```text
data-service:8000
```

The service listens on container port 8000 and is exposed on host port 8002.

## API behaviour

The service exposes health and CRUD endpoints for notes. Write requests use a validated payload with these limits:

```text
title:   1 to 120 characters
content: 1 to 10000 characters
```

Validation errors are generated before a valid persistence operation is completed and are distinct from transport failures to `data-service`.

## Request correlation

The service reuses the incoming `X-Request-ID` when present and creates one otherwise. The same identifier is propagated to `data-service`.

## Important log events

`downstream_request_completed` records the status and latency of calls to `data-service`.

`data_service_unavailable` means the HTTP client could not successfully contact `data-service` and no normal downstream response was obtained.

Successful operations also produce events such as:

- `notes_listed`;
- `note_created`;
- `note_updated`;
- `note_deleted`.

`service_started` and `service_stopped` provide lifecycle context.

## Error propagation

When the request to `data-service` raises a client/network error, processing-service returns HTTP 503. When data-service returns an HTTP error response, processing-service propagates the downstream status and available error detail.

The presence of an error in processing-service logs therefore describes where the error was observed and how the application handled it; it does not by itself identify the root cause.

## Network observations

The configured dependency probe is:

```text
source: processing-service
target: data-service:8000
HTTP path: /docs
```

The corresponding detector is:

```text
NETLAT-processing-service-data-service
```

It is SINGLE_ENTITY and represents only this source/link.

## Resource telemetry

Container CPU is observed through:

```text
docker_container_cpu.usage_percent
```

Container memory is observed through:

```text
docker_container_mem.usage_percent
```

These metrics describe resource usage of the processing-service container.

## Timing semantics

Application timing and network timing are distinct observations:

- api-gateway `downstream_request_completed.latency_ms` measures the complete call from api-gateway to processing-service;
- processing-service `downstream_request_completed.latency_ms` measures the call from processing-service to data-service;
- `network_service_latency.response_time` measures the configured active service probe;
- `ping` measures ICMP timing.

These values can overlap in time but they represent different parts of the request and observation path.
