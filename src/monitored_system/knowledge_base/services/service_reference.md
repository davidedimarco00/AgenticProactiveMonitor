---
kb_id: monitored-system.services.reference
version: 2
domain: monitored_system
document_type: service-reference
agents: [coordinator, evidence, reasoning, critic, remediation]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/api-gateway/app.py, src/monitored_system/src/processing-service/app.py, src/monitored_system/src/data-service/app.py, src/monitored_system/src/traffic-generator/generator.py, src/monitored_system/infrastructure/entrypoint.sh, src/monitored_system/infrastructure/telegraf.conf]
---

# Service Reference

## traffic-generator

Purpose: synthetic user workload.

It sends real HTTP requests to `api-gateway` every 2-5 seconds by default. Actions include dashboard browsing, note creation, note reading, note updates and occasional deletion. It generates `X-Request-ID` values and records action latency.

Important logs:

- `synthetic_user_action_completed`
- `synthetic_user_action_failed`

Direct application target: `api-gateway:5000`.

Active network-service probe:

```text
target: api-gateway:5000
path:   /notes/new
```

This is one of the three critical NETLAT source/link detectors.

A traffic-generator failure is usually a symptom source. When it reports errors, inspect the downstream chain before declaring the generator as root cause.

## api-gateway

Purpose: Flask web interface and upstream gateway.

Host port: 8080. Container port: 5000.

Downstream: `processing-service:8000`.

Important endpoints:

- `GET /health`
- `GET /`
- `GET|POST /notes/new`
- `GET /notes/<id>`
- `GET|POST /notes/<id>/edit`
- `POST /notes/<id>/delete`

Important logs:

- `http_request_completed`
- `downstream_request_completed`
- `notes_service_unavailable`
- `request_failed`

Active network-service probe:

```text
target: processing-service:8000
path:   /docs
```

This is one of the three critical NETLAT source/link detectors. `api-gateway` also has `NET_ADMIN` because it is the source used by the controlled `tc/netem` network-latency scenario.

If processing-service is unreachable, the gateway can return HTTP 503.

## processing-service

Purpose: FastAPI application layer for note operations.

Host port: 8002. Container port: 8000.

Downstream: `data-service:8000`.

It validates note payloads and forwards health, list, get, create, update and delete requests. It also supports the controlled application-delay fault through `/var/run/monitored-faults/processing-delay-ms`.

Important logs:

- `downstream_request_completed`
- `data_service_unavailable`
- `fault_delay_applied`
- `notes_listed`
- `note_created`
- `note_updated`
- `note_deleted`

Active network-service probe:

```text
target: data-service:8000
path:   /docs
```

This is one of the three critical NETLAT source/link detectors.

This service is the target of the CPU-spike and high-application-latency scenarios.

## data-service

Purpose: persistence layer.

Host port: 8003. Container port: 8000.

Storage: SQLite database at `/var/lib/notes/notes.db` on the persistent `notes-data` volume.

Important endpoints:

- `GET /health`
- `GET /notes`
- `GET /notes/{id}`
- `POST /notes`
- `PUT /notes/{id}`
- `DELETE /notes/{id}`

Important logs include `note_created`, `note_read`, `note_updated`, `note_deleted`, `notes_listed` and `note_not_found`.

Active diagnostic network-service probe:

```text
target: processing-service:8000
path:   /docs
```

This probe is collected as telemetry but is not one of the three configured NETLAT detectors.

If this service is unavailable, failures propagate to processing-service and then upstream.

## worker-service

Purpose: independent synthetic background node.

It runs the generic synthetic workload from the monitored container entrypoint rather than a Notes HTTP API. It emits synthetic application events and system heartbeats. It is intentionally not part of the request chain.

Active diagnostic network-service probe:

```text
target: api-gateway:5000
path:   /notes/new
```

This probe is collected as telemetry but is not one of the three configured NETLAT detectors.

This service is the target of the controlled memory-leak scenario. High worker memory should not be interpreted as direct proof of a Notes request-path failure.

## Network probe naming

All service probes above are stored using the Telegraf measurement:

```text
network_service_latency
```

The main timing field is `network_service_latency.response_time`; `network_service_latency.result_code` provides probe-status evidence.
