---
kb_id: monitored-system.service.data-service
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
    persistence,
    sqlite,
    availability,
    error-propagation,
    request-correlation,
  ]
services: [processing-service, data-service]
incident_types:
  [
    availability,
    downstream-failure,
    application-latency,
    network-latency,
    cpu,
    memory,
  ]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/src/data-service/app.py,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# data-service Service

## Purpose

`data-service` is the persistence layer of the Notes Platform. It owns the SQLite database and serves note CRUD operations to `processing-service`.

The service listens on container port 8000 and is exposed on host port 8003.

## Persistence

The SQLite database is stored at:

```text
/var/lib/notes/notes.db
```

The path is backed by the persistent Docker volume `notes-data`.

The `notes` table contains:

- `id`;
- `title`;
- `content`;
- `created_at`;
- `updated_at`.

## Health behaviour

`GET /health` opens the database and executes a simple `SELECT 1` query.

A successful response means that the data-service process handled the health request and SQLite was accessible for that check. If SQLite raises an error, the endpoint returns HTTP 503 with a database-unavailable condition.

## Request correlation

The service reuses the incoming `X-Request-ID` when present and generates one otherwise. This identifier is written to application logs for note operations.

## Important log events

Normal persistence activity includes:

- `notes_listed`;
- `note_read`;
- `note_created`;
- `note_updated`;
- `note_deleted`.

`note_not_found` is emitted when a requested note does not exist and corresponds to an HTTP 404 condition.

`service_started` and `service_stopped` provide lifecycle context.

## Dependency relationship

`processing-service` depends directly on data-service. If the call from processing-service cannot obtain a valid downstream response, processing-service can return an error to its own caller. That result can then be observed further upstream at api-gateway and traffic-generator.

The application dependency direction is:

```text
api-gateway -> processing-service -> data-service
```

## Network context

The critical application link leading to this service is:

```text
processing-service -> data-service:8000
```

The corresponding detector is:

```text
NETLAT-processing-service-data-service
```

It is SINGLE_ENTITY and represents only that configured source/link.

A failed application request to data-service and a failed network probe are different observations. The former is produced by application communication; the latter is produced by the configured Telegraf probe.

## Resource telemetry

Container CPU and memory are available through:

```text
docker_container_cpu.usage_percent
docker_container_mem.usage_percent
```

These fields describe the data-service container. SQLite availability is separately exposed by the `/health` database check and application behaviour.
