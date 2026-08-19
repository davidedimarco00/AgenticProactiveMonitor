---
kb_id: monitored-system.domain.software-behavior
version: 2
domain: monitored_system
document_type: domain-reference
roles: [software_developer, application_engineer]
domains:
  [software, api, validation, error-handling, persistence, application-logs]
services: [api-gateway, processing-service, data-service]
incident_types: [application-latency, availability, downstream-failure]
source_files:
  [
    src/monitored_system/src/api-gateway/app.py,
    src/monitored_system/src/processing-service/app.py,
    src/monitored_system/src/data-service/app.py,
  ]
---

# Software Behaviour Reference

## API contract

The Notes Platform exposes create, read, update, delete and list operations through:

```text
api-gateway -> processing-service -> data-service
```

`api-gateway` provides the web interface, `processing-service` provides the Notes application API and `data-service` persists the data.

## Input validation

The web layer requires non-empty title and content values for note create/update operations.

At the processing layer, note payloads are validated with these limits:

```text
title:   1 to 120 characters
content: 1 to 10000 characters
```

Validation errors are part of the application contract and are different from network transport failures or service unavailability.

## Request identifiers

The services accept an `X-Request-ID` header and propagate it downstream. If the identifier is absent, a new one is generated.

The request identifier provides a common key for application events produced by different services while handling the same operation.

## api-gateway error handling

When the HTTP client cannot obtain a normal response from `processing-service`, api-gateway records:

```text
event_type = notes_service_unavailable
```

and returns HTTP 503 for the affected web request.

When the downstream service responds, api-gateway records `downstream_request_completed` with the downstream status code and latency. A downstream 404 is propagated as 404, while other error responses are propagated according to the application code.

## processing-service error handling

`processing-service` forwards persistence requests to `data-service`.

When the client call to `data-service` raises a request error, processing-service records:

```text
event_type = data_service_unavailable
```

and returns HTTP 503.

When `data-service` responds with an HTTP error, processing-service propagates the downstream status and available error detail. Successful note operations produce service events such as `note_created`, `note_updated`, `note_deleted` and `notes_listed`.

## data-service persistence behaviour

`data-service` stores notes in SQLite at:

```text
/var/lib/notes/notes.db
```

The `notes` table contains:

- `id`;
- `title`;
- `content`;
- `created_at`;
- `updated_at`.

The `/health` endpoint opens SQLite and executes `SELECT 1`. If SQLite raises an error, health returns HTTP 503 with a database-unavailable condition.

A missing note returns HTTP 404. This response is a defined application condition and is not equivalent to service unavailability.

## HTTP status semantics in this application

Relevant status codes include:

- `400`: invalid web form or request input at the gateway layer;
- `404`: requested note does not exist;
- `503`: a required downstream operation or database access could not be completed in the implemented error path.

The same status code can be observed by more than one service because statuses may be propagated upstream.

## Log semantics and software scope

An application log event records what the emitting service observed or did. For example, `data_service_unavailable` states that processing-service could not complete its client request to data-service; the event itself does not distinguish network failure, destination process state or another lower-level cause.

Likewise, an upstream 5xx response can contain a downstream result. Service name, `request_id`, `downstream`, timestamps and status fields preserve the context needed for the agent to reason about that behaviour.

## Software knowledge boundary

This document describes the software contract and implemented error-handling paths. It does not define which runtime symptom pattern constitutes a software bug or any other incident diagnosis.
