---
kb_id: monitored-system.shared.system-architecture
version: 3
domain: monitored_system
document_type: architecture
roles: [technical_lead, system_engineer, network_engineer, application_engineer, software_developer]
domains: [architecture, dependencies, services, networks, persistence]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/api-gateway/app.py, src/monitored_system/src/processing-service/app.py, src/monitored_system/src/data-service/app.py, src/monitored_system/src/traffic-generator/generator.py]
---

# Notes Platform Architecture

## Purpose

The monitored system is a distributed Notes Platform. It runs independently from the monitoring and agentic infrastructure and exposes application behaviour that can be observed through metrics and logs.

## Runtime request path

The normal HTTP request path is:

```text
traffic-generator
       |
       v
api-gateway
       |
       v
processing-service
       |
       v
data-service
```

`worker-service` is an independent background service. It is not part of the Notes HTTP request path.

## Service responsibilities

- `traffic-generator`: behaves as a synthetic client and sends real user-like operations to the platform.
- `api-gateway`: Flask web front end and gateway. It receives requests and forwards note operations to `processing-service`.
- `processing-service`: FastAPI application layer. It handles note operations and communicates with `data-service` for persistence.
- `data-service`: FastAPI persistence layer backed by SQLite.
- `worker-service`: independent background workload that does not participate in the Notes request chain.

## Ports and internal communication

- `api-gateway`: container port 5000, host port 8080.
- `processing-service`: container port 8000, host port 8002.
- `data-service`: container port 8000, host port 8003.

Internal dependencies are:

```text
traffic-generator  -> api-gateway:5000
api-gateway        -> processing-service:8000
processing-service -> data-service:8000
```

A fault in a downstream service can therefore generate symptoms in upstream services. For example, a data-service failure can first appear as a downstream error in processing-service and later as an HTTP failure at api-gateway.

## Persistence

`data-service` stores notes in SQLite at:

```text
/var/lib/notes/notes.db
```

The database is mounted on the persistent Docker volume `notes-data`.

## Request correlation

Requests carry an `X-Request-ID`. The identifier is created by the traffic generator or API Gateway and propagated through the request path. The `request_id` field can be used to correlate logs emitted by different services for the same operation.

## Networks

Each monitored container joins:

- `monitored-system-net`, used for application communication;
- `observability-net`, used to reach the external monitoring infrastructure.

The presence of both networks is important during diagnosis because application-path problems and observability-path problems are not equivalent.

## Normal service relationships

During normal operation:

- the four request-path components communicate in the order shown above;
- `worker-service` remains independent from the HTTP path;
- `traffic-generator` continuously produces user-like requests;
- Telegraf and Fluent Bit inside the monitored containers export telemetry to OpenSearch.

When diagnosing an incident, use the real runtime dependency direction. An upstream error does not by itself prove that the upstream component is the root cause.
