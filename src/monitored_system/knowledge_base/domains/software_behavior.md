---
kb_id: monitored-system.domain.software-behavior
version: 1
domain: monitored_system
document_type: domain-reference
roles: [software_developer, application_engineer]
domains: [software, api, validation, error-handling, persistence, application-logs]
services: [api-gateway, processing-service, data-service]
incident_types: [application-latency, availability, downstream-failure]
source_files: [src/monitored_system/src/api-gateway/app.py, src/monitored_system/src/processing-service/app.py, src/monitored_system/src/data-service/app.py]
---

# Software Behaviour Reference

## API contract

The Notes Platform exposes create, read, update, delete and list operations through the request chain:

```text
api-gateway -> processing-service -> data-service
```

`api-gateway` provides the web interface, `processing-service` provides the Notes application API and `data-service` persists the data.

## Input validation

The application requires non-empty note titles and content.

At the processing layer, note payloads are validated with these limits:

```text
title:   1 to 120 characters
content: 1 to 10000 characters
```

Validation failures should be distinguished from infrastructure, network or downstream availability problems.

## Request identifiers

The services accept an `X-Request-ID` header and propagate it downstream. If the identifier is absent, a new one is generated.

This means that application errors and timing information for one operation can be correlated across the service chain using the same request identifier.

## api-gateway error handling

When `api-gateway` cannot contact `processing-service`, it records:

```text
event_type = notes_service_unavailable
```

and returns HTTP 503 for the affected web request.

When the downstream service responds, `api-gateway` records `downstream_request_completed` with the downstream status code and latency. A downstream 404 is propagated as a 404, while other error responses are propagated with their HTTP status.

Therefore, a 503 at the gateway is not sufficient evidence of a gateway software defect.

## processing-service error handling

`processing-service` forwards requests to `data-service`.

When the network/client request to `data-service` fails, it records:

```text
event_type = data_service_unavailable
```

and returns HTTP 503.

When `data-service` responds with an HTTP error, processing-service propagates the downstream status and available error detail. Successful note operations produce service-specific events such as `note_created`, `note_updated`, `note_deleted` and `notes_listed`.

## data-service persistence behaviour

`data-service` stores notes in SQLite at:

```text
/var/lib/notes/notes.db
```

The `notes` table contains:

- `id`
- `title`
- `content`
- `created_at`
- `updated_at`

The `/health` endpoint performs a simple database query. If SQLite cannot be accessed, health returns HTTP 503 with a database-unavailable error.

A missing note returns HTTP 404. This normal application condition should not be confused with service unavailability.

## Status interpretation

Useful distinctions include:

- `400`: invalid web form or request input can be an application/client problem rather than infrastructure failure;
- `404`: requested note does not exist;
- `503`: a required downstream service or database may be unavailable;
- `5xx` with correlated downstream errors: investigate dependency failure before assuming a local software bug.

## Code-level diagnostic principles

When software behaviour is suspected:

- correlate stack/error information with `request_id` and service name;
- distinguish expected validation and 404 responses from unexpected failures;
- inspect whether the error was generated locally or propagated from a dependency;
- compare application logs with network and runtime evidence before attributing a symptom to code;
- use repeated service-specific errors or invalid behaviour relative to the API contract as stronger evidence of a software defect;
- do not treat an error message emitted by an upstream service as proof that the upstream software caused the incident.
