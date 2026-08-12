---
kb_id: monitored-system.shared.dependency-impact
version: 1
domain: monitored_system
document_type: architecture
roles: [technical_lead, system_engineer, network_engineer, application_engineer, software_developer]
domains: [architecture, dependencies, impact, causality, correlation]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability, downstream-failure]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/src/api-gateway/app.py, src/monitored_system/src/processing-service/app.py, src/monitored_system/src/data-service/app.py, src/monitored_system/src/traffic-generator/generator.py]
---

# Dependency and Impact Model

## Request-path dependency graph

The Notes Platform has one main user-facing request chain:

```text
traffic-generator
      -> api-gateway
      -> processing-service
      -> data-service
```

`worker-service` is independent from this path.

Dependency direction matters during diagnosis. A downstream failure can generate visible symptoms in several upstream components even when those upstream processes are themselves healthy.

## Direct dependencies

```text
traffic-generator  depends on api-gateway
api-gateway        depends on processing-service
processing-service depends on data-service
data-service       has no application-service dependency
worker-service     is independent from the Notes request chain
```

The SQLite database used by data-service is an internal persistence dependency of data-service.

## Propagation model

A failure can propagate upstream as follows:

```text
data-service unavailable
    -> processing-service cannot complete persistence request
    -> api-gateway receives downstream failure
    -> traffic-generator observes failed user action
```

This means that the component with the most visible error is not necessarily the component where the failure started.

A useful diagnostic rule is to look for the earliest abnormal evidence that can explain the later symptoms.

## Local resource problems

CPU or memory anomalies are initially local observations. A resource anomaly on one container should not be treated as proof of cross-service impact.

A local resource problem becomes relevant to the full request path when additional evidence shows consequences such as:

- increased request latency;
- failed requests;
- downstream timeouts;
- service unavailability;
- correlated abnormalities in dependent services.

## Network-link problems

The three critical request-path links are:

```text
traffic-generator  -> api-gateway
api-gateway        -> processing-service
processing-service -> data-service
```

A link problem should be associated with its source and target. The corresponding NETLAT detector is SINGLE_ENTITY and represents only that configured source/link.

Network degradation can produce application latency upstream, but application latency alone is not proof of a network problem. Raw ICMP RTT and `network_service_latency.response_time` provide independent link-oriented evidence.

## Application-processing problems

A service can remain reachable while processing requests slowly or incorrectly. In that case:

- network probes can remain normal;
- the process can remain running;
- application latency or error logs can become abnormal.

This is why runtime availability, network reachability and correct application behaviour must be treated as separate dimensions.

## Missing telemetry

Missing metrics or logs can support an availability hypothesis, but they can also result from an observability-path problem.

Therefore:

```text
missing telemetry != confirmed service failure
```

When possible, combine missing telemetry with independent runtime state, health, network or upstream/downstream evidence.

## Request-level correlation

`X-Request-ID` is propagated along the application path. For one affected operation, the same `request_id` can connect:

- the traffic-generator action;
- api-gateway request and downstream timing;
- processing-service downstream timing;
- data-service persistence activity.

This is stronger than correlating unrelated log messages only by approximate timestamp.

## Cross-domain diagnosis principles

When multiple explanations are possible:

- distinguish local evidence from propagated impact;
- follow the actual dependency direction;
- separate resource, network, application and persistence signals;
- prefer evidence that directly observes the suspected component or link;
- use upstream symptoms to measure impact, not automatically to identify origin;
- keep `worker-service` separate from the Notes request path unless live evidence demonstrates shared impact;
- treat retrieved knowledge as a model of expected relationships, while current telemetry remains the source of truth for the incident.
