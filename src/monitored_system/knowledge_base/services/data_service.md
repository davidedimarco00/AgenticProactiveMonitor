---
kb_id: monitored-system.service.data-service
version: 1
domain: monitored_system
document_type: service-reference
roles: [technical_lead, system_engineer, network_engineer, application_engineer, software_developer]
domains: [application, persistence, sqlite, availability, error-propagation, request-correlation]
services: [processing-service, data-service]
incident_types: [availability, downstream-failure, application-latency, network-latency, cpu, memory]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/data-service/app.py, src/monitored_system/infrastructure/telegraf.conf]
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

A successful health response therefore confirms that the service can access SQLite at that moment. If SQLite raises an error, the endpoint returns HTTP 503 with a database-unavailable condition.

## Request correlation

The service reuses the incoming `X-Request-ID` when present and generates one otherwise. This identifier is written to application logs for note operations.

## Important log events

Normal persistence activity includes:

- `notes_listed`;
- `note_read`;
- `note_created`;
- `note_updated`;
- `note_deleted`.

`note_not_found` is a warning for a requested note that does not exist. It corresponds to a normal HTTP 404 condition and is not, by itself, evidence that data-service is unavailable.

`service_started` and `service_stopped` provide lifecycle context.

## Upstream impact

`processing-service` depends directly on data-service. If data-service is unavailable or cannot be reached, processing-service can emit `data_service_unavailable` and return HTTP 503. This can then propagate to api-gateway and traffic-generator.

The dependency chain is:

```text
data-service problem
    -> processing-service failure
    -> api-gateway failure
    -> synthetic user failure
```

Upstream visibility does not change the downstream origin of the problem.

## Network context

The critical application link leading to this service is:

```text
processing-service -> data-service:8000
```

The corresponding detector is:

```text
NETLAT-processing-service-data-service
```

It is SINGLE_ENTITY and represents only the source/link above.

A failed application call to data-service can be caused by the destination service, its SQLite dependency or the network path. These possibilities should be separated using probe results, service telemetry, health evidence and application logs.

## Diagnostic interpretation

Evidence for a data-service or persistence problem becomes stronger when:

- fresh data-service activity disappears or health fails;
- processing-service reports `data_service_unavailable`;
- the network path is normal but database-related health or application behaviour is abnormal;
- correlated upstream 503 responses appear after the downstream failure.

Evidence for a network problem becomes stronger when source-side network probes to data-service are abnormal while the destination remains otherwise healthy.
