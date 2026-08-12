---
kb_id: monitored-system.service.processing-service
version: 1
domain: monitored_system
document_type: service-reference
roles: [technical_lead, system_engineer, network_engineer, application_engineer, software_developer]
domains: [application, api, validation, downstream, latency, error-propagation, request-correlation]
services: [api-gateway, processing-service, data-service]
incident_types: [cpu, application-latency, availability, downstream-failure, network-latency, memory]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/processing-service/app.py, src/monitored_system/infrastructure/telegraf.conf]
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

Validation failures are application-level input problems and should be separated from infrastructure, network or downstream availability incidents.

## Request correlation

The service reuses the incoming `X-Request-ID` when present and creates one otherwise. The same identifier is propagated to `data-service`.

This permits one request to be correlated across `api-gateway`, `processing-service` and `data-service`.

## Important log events

`downstream_request_completed` records the status and latency of calls to `data-service`.

`data_service_unavailable` means the HTTP client could not successfully contact `data-service`.

Successful operations also produce application-specific events such as:

- `notes_listed`;
- `note_created`;
- `note_updated`;
- `note_deleted`.

`service_started` and `service_stopped` provide lifecycle context.

## Error propagation

When `data-service` cannot be contacted, processing-service returns HTTP 503. When data-service returns an HTTP error, processing-service propagates that downstream status and available detail.

Therefore, an error emitted by processing-service can be a local problem or a propagated downstream problem. The distinction requires data-service and network evidence.

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

## Resource interpretation

Container CPU is observed through:

```text
docker_container_cpu.usage_percent
```

Container memory is observed through:

```text
docker_container_mem.usage_percent
```

A local resource anomaly should be correlated with request timing and downstream evidence before being declared the root cause of user-visible latency.

## Latency interpretation

Slow requests observed at processing-service can originate from different domains:

- local application processing;
- local CPU or memory pressure;
- network degradation towards data-service;
- slow or unavailable data-service.

Useful discrimination comes from comparing processing-service runtime metrics, application logs, `processing-service -> data-service` probes and correlated upstream latency at api-gateway.

## Diagnostic interpretation

A local processing-service hypothesis becomes stronger when the first abnormal evidence appears in its own runtime or application behaviour while downstream network and data-service evidence remain normal.

A downstream hypothesis becomes stronger when `data_service_unavailable`, failed probes, missing data-service activity or downstream 5xx evidence appears before the upstream symptom.
