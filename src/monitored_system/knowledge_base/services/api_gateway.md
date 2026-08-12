---
kb_id: monitored-system.service.api-gateway
version: 1
domain: monitored_system
document_type: service-reference
roles: [technical_lead, system_engineer, network_engineer, application_engineer, software_developer]
domains: [application, gateway, network, http, error-propagation, request-correlation]
services: [api-gateway, processing-service]
incident_types: [availability, network-latency, application-latency, downstream-failure, cpu, memory]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/api-gateway/app.py, src/monitored_system/infrastructure/telegraf.conf]
---

# api-gateway Service

## Purpose

`api-gateway` is the web-facing entry point of the Notes Platform. It renders the web interface and forwards note operations to `processing-service`.

Its direct downstream dependency is:

```text
processing-service:8000
```

The service listens on container port 5000 and is exposed on host port 8080.

## Main endpoints

Important routes include:

- `GET /health`;
- `GET /`;
- `GET|POST /notes/new`;
- `GET /notes/<id>`;
- `GET|POST /notes/<id>/edit`;
- `POST /notes/<id>/delete`.

The health endpoint is not purely local: it calls `processing-service /health`. Therefore, an unhealthy health response can be caused by a downstream problem.

## Request correlation

If an incoming request has `X-Request-ID`, the gateway reuses it. Otherwise it creates a new identifier. The same identifier is sent to `processing-service` and returned in the response header.

This makes `request_id` the primary correlation key between user-visible failures at the gateway and downstream application evidence.

## Important log events

`http_request_completed` records the final web request status and total gateway-side latency.

`downstream_request_completed` records the request sent to `processing-service`, including downstream status and latency.

`notes_service_unavailable` means that the HTTP client could not contact `processing-service` or the request failed before receiving a valid response.

`request_failed` is emitted when a user-facing request returns HTTP 503 because a required downstream service was unavailable.

## Error propagation

A 503 at `api-gateway` is not sufficient evidence that the gateway is the root cause.

If the gateway cannot contact `processing-service`, it returns HTTP 503. If processing-service itself returns an error status, the gateway propagates the downstream status through the web request path.

A useful diagnostic distinction is therefore:

```text
gateway process healthy != complete application path healthy
```

## Network observations

The configured dependency probe is:

```text
source: api-gateway
target: processing-service:8000
HTTP path: /docs
```

The corresponding anomaly detector is:

```text
NETLAT-api-gateway-processing-service
```

It is SINGLE_ENTITY and represents only this source/link.

Raw ICMP RTT and `network_service_latency.response_time` should be compared with `downstream_request_completed.latency_ms` when distinguishing network delay from application processing delay.

## Diagnostic interpretation

Evidence supporting a local gateway issue includes abnormal gateway runtime/resource behaviour with normal downstream connectivity and normal processing-service evidence.

Evidence supporting a downstream issue includes:

- `notes_service_unavailable`;
- abnormal network evidence towards processing-service;
- processing-service errors correlated by `request_id`;
- a processing-service or deeper dependency failure that precedes the gateway 5xx response.

The root cause should be assigned to the earliest component or link whose live evidence explains the propagated failure.
