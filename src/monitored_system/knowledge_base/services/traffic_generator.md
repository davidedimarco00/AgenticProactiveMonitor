---
kb_id: monitored-system.service.traffic-generator
version: 1
domain: monitored_system
document_type: service-reference
roles: [technical_lead, system_engineer, network_engineer, application_engineer, software_developer]
domains: [application, synthetic-traffic, network, observability, request-correlation]
services: [traffic-generator, api-gateway]
incident_types: [availability, network-latency, application-latency, cpu, memory]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/traffic-generator/generator.py, src/monitored_system/infrastructure/telegraf.conf]
---

# traffic-generator Service

## Purpose

`traffic-generator` represents a continuous external user workload for the Notes Platform. It sends real HTTP requests to `api-gateway` and records the result of each synthetic user action.

It is not part of the business logic of the Notes application. Its main operational value during diagnosis is that it provides an upstream view of user-visible success, failure and latency.

## Runtime configuration

The service runs in application mode and targets:

```text
http://api-gateway:5000
```

Requests normally use a timeout of 5 seconds. The interval between actions is normally between 2 and 5 seconds.

## User-like actions

The generator performs a weighted mix of operations:

- browse the dashboard;
- create a note;
- read an existing note;
- update an existing note;
- delete a note.

The workload keeps an in-memory set of observed note identifiers. Missing or stale note identifiers can produce normal 404 handling and should not automatically be treated as infrastructure failure.

## Request correlation

Every generated action creates an `X-Request-ID`. The same identifier is sent to `api-gateway` and can be propagated through the application chain.

This makes the traffic generator a useful starting point for reconstructing one affected request across multiple services.

## Application logs

Successful and failed actions are recorded using:

```text
synthetic_user_action_completed
synthetic_user_action_failed
```

Useful fields include:

- `request_id`;
- `action`;
- `method`;
- `path`;
- `status_code` when a response is received;
- `latency_ms`;
- `error_type` when the request itself fails.

A 5xx response is recorded as a failed synthetic user action. A request exception is also recorded as a failure, but without an HTTP status code.

## Network observations

The configured network target is `api-gateway`.

```text
ICMP target:        api-gateway
TCP/HTTP target:    api-gateway:5000
probe path:         /notes/new
```

The corresponding critical detector is:

```text
NETLAT-traffic-generator-api-gateway
```

The detector is SINGLE_ENTITY and must only be interpreted for the `traffic-generator -> api-gateway` link.

## Diagnostic interpretation

A traffic-generator failure is often an upstream symptom rather than the root cause. When actions fail or become slow, useful follow-up questions are:

- Is `api-gateway` reachable?
- Is the `traffic-generator -> api-gateway` network probe abnormal?
- Does `api-gateway` return a downstream 5xx error?
- Can the same `request_id` be followed into processing-service and data-service?

If traffic-generator latency rises while all downstream service and network evidence remains normal, then the generator itself or its runtime becomes more relevant. Otherwise, prefer the first downstream component whose live evidence explains the symptom.
