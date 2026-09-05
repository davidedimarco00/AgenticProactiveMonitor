---
kb_id: monitored-system.service.traffic-generator
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
  [application, synthetic-traffic, network, observability, request-correlation]
services: [traffic-generator, api-gateway]
incident_types:
  [availability, network-latency, application-latency, cpu, memory]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/src/traffic-generator/generator.py,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# traffic-generator Service

## Purpose

`traffic-generator` represents a continuous external user workload for the Notes Platform. It sends real HTTP requests to `api-gateway` and records the result of each synthetic user action.

It does not implement Notes business logic. It provides an upstream observation point for request status and end-to-end user-visible latency.

## Runtime configuration

The service targets:

```text
http://api-gateway:5000
```

The default request timeout is 5 seconds. The interval between actions is normally between 2 and 5 seconds.

## User-like actions

The generator performs a weighted mix of operations:

- browse the dashboard;
- create a note;
- read an existing note;
- update an existing note;
- delete a note.

It keeps an in-memory set of observed note identifiers. A note that no longer exists can produce HTTP 404 during normal workload execution.

## Request correlation

Every generated action creates an `X-Request-ID`. The identifier is sent to `api-gateway` and can be propagated through the application chain.

## Application logs

Successful and failed actions use:

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

The configured network target is `api-gateway`:

```text
ICMP target: api-gateway
TCP target:  api-gateway:5000
HTTP probe:  http://api-gateway:5000/notes/new
```

Transport-level telemetry for this link is:

```text
measurement: network_transport_latency
field:       network_transport_latency.response_time
target tag:  tag.network_target = api-gateway
```

The corresponding SINGLE_ENTITY transport detector is:

```text
NETLAT-traffic-generator-api-gateway
```

Application-service timing is collected separately as:

```text
measurement: application_service_latency
field:       application_service_latency.response_time
```

The corresponding SINGLE_ENTITY application-latency detector is:

```text
APPLAT-traffic-generator-api-gateway
```

## Interpretation boundaries

A request result observed by traffic-generator describes the outcome visible at the client side. It does not identify which component in the downstream chain caused that result.

`network_transport_latency.response_time` measures TCP connection establishment to the gateway, while `application_service_latency.response_time` measures the configured HTTP probe. The traffic-generator application `latency_ms` includes the complete user-visible request path. These values are therefore different observations and must not be treated as equivalent.
