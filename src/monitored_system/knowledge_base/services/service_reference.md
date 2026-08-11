---
kb_id: monitored-system.services.reference
version: 4
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
domains: [services, dependencies, api, logs, network]
services:
  [
    traffic-generator,
    api-gateway,
    processing-service,
    data-service,
    worker-service,
  ]
incident_types:
  [cpu, memory, network-latency, application-latency, availability]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/src/api-gateway/app.py,
    src/monitored_system/src/processing-service/app.py,
    src/monitored_system/src/data-service/app.py,
    src/monitored_system/src/traffic-generator/generator.py,
    src/monitored_system/infrastructure/entrypoint.sh,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# Service Reference

## traffic-generator

Purpose: synthetic user workload.

It sends real HTTP requests to `api-gateway` every 2-5 seconds by default. Actions include dashboard browsing, note creation, note reading, note updates and occasional deletion. It generates `X-Request-ID` values and records action latency.

Important logs:

- `synthetic_user_action_completed`
- `synthetic_user_action_failed`

Direct application target:

```text
api-gateway:5000
```

Active network-service probe:

```text
target: api-gateway:5000
path:   /notes/new
```

Failures reported by the traffic generator can be symptoms of downstream problems. The request path should be inspected before assigning the root cause to this component.

## api-gateway

Purpose: Flask web interface and upstream gateway.

Host port: 8080. Container port: 5000.

Downstream dependency:

```text
processing-service:8000
```

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

If `processing-service` is unreachable or fails to answer correctly, api-gateway can return HTTP 503. Such an error can therefore be a propagated symptom rather than a gateway root cause.

## processing-service

Purpose: FastAPI application layer for note operations.

Host port: 8002. Container port: 8000.

Downstream dependency:

```text
data-service:8000
```

The service validates note payloads and forwards persistence work to `data-service`.

Important logs:

- `downstream_request_completed`
- `data_service_unavailable`
- note-operation events such as `notes_listed`, `note_created`, `note_updated` and `note_deleted`

Active network-service probe:

```text
target: data-service:8000
path:   /docs
```

High resource usage, local processing delay or downstream failures can all affect request latency. Network evidence and data-service evidence should be checked before attributing slow requests to processing-service itself.

## data-service

Purpose: persistence layer.

Host port: 8003. Container port: 8000.

Storage:

```text
/var/lib/notes/notes.db
```

The SQLite database is stored on the persistent `notes-data` Docker volume.

Important endpoints:

- `GET /health`
- `GET /notes`
- `GET /notes/{id}`
- `POST /notes`
- `PUT /notes/{id}`
- `DELETE /notes/{id}`

Important logs include:

- `note_created`
- `note_read`
- `note_updated`
- `note_deleted`
- `notes_listed`
- `note_not_found`

If this service becomes unavailable, errors can propagate first to `processing-service`, then to `api-gateway`, and finally to the synthetic client.

## worker-service

Purpose: independent background workload.

`worker-service` does not participate in the Notes HTTP request chain. It emits its own application events and system heartbeats and is monitored as an independent service.

Its telemetry should normally be interpreted locally. A resource anomaly on worker-service is not sufficient evidence of a Notes request-path fault unless other live evidence shows shared resource contention or impact on additional services.

## Network probe naming

Active service probes are stored using the Telegraf measurement:

```text
network_service_latency
```

The main timing field is:

```text
network_service_latency.response_time
```

Probe outcome is available in:

```text
network_service_latency.result_code
```
