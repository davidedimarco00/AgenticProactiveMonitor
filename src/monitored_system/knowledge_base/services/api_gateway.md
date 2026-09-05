---
kb_id: monitored-system.service.api-gateway
version: 3
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
  [application, gateway, network, http, error-propagation, request-correlation]
services: [api-gateway, processing-service]
incident_types:
  [
    availability,
    network-latency,
    application-latency,
    downstream-failure,
    cpu,
    memory,
  ]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/src/api-gateway/app.py,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
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

The `/health` endpoint calls `processing-service /health`. Its result therefore reflects more than the local Flask process.

## Request correlation

If an incoming request has `X-Request-ID`, the gateway reuses it. Otherwise it creates a new identifier. The same identifier is sent to `processing-service` and returned in the response header.

## Important log events

`http_request_completed` records the final web request status and total gateway-side latency.

`downstream_request_completed` records the call to `processing-service`, including downstream status and latency.

`notes_service_unavailable` is emitted when the HTTP request to `processing-service` fails before a valid response is received.

`request_failed` is emitted when a user-facing request returns HTTP 503 because a required downstream operation could not be completed.

## Error propagation

When the gateway cannot contact `processing-service`, it returns HTTP 503. When `processing-service` returns an error status, that status is propagated through the gateway according to the application code.

Therefore the HTTP status emitted by the gateway can represent either local gateway behaviour or a downstream result. The log field `downstream` identifies `processing-service` for the forwarded call.

## Network observations

The configured downstream target is:

```text
source: api-gateway
target: processing-service:8000
```

Transport-level telemetry for this link is:

```text
measurement: network_transport_latency
field:       network_transport_latency.response_time
target tag:  tag.network_target = processing-service
```

This is TCP connection-establishment timing and is the telemetry used by the corresponding SINGLE_ENTITY detector:

```text
NETLAT-api-gateway-processing-service
```

Application-service timing is collected separately with an HTTP probe to `/docs`:

```text
measurement: application_service_latency
field:       application_service_latency.response_time
```

The corresponding application-latency detector is:

```text
APPLAT-api-gateway-processing-service
```

Raw ICMP RTT, `network_transport_latency.response_time`, `application_service_latency.response_time` and `downstream_request_completed.latency_ms` are different observations. They respectively represent ICMP timing, TCP connection establishment, the configured HTTP service probe and application-call timing produced by the gateway.

## Resource telemetry

Container CPU and memory are available through:

```text
docker_container_cpu.usage_percent
docker_container_mem.usage_percent
```

These fields describe resource usage of the api-gateway container and do not by themselves describe downstream service state.
