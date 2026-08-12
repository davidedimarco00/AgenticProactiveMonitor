---
kb_id: monitored-system.shared.dependency-impact
version: 2
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

## Direct dependencies

```text
traffic-generator  depends on api-gateway
api-gateway        depends on processing-service
processing-service depends on data-service
data-service       has no application-service dependency
worker-service     is independent from the Notes request chain
```

The SQLite database used by data-service is an internal persistence dependency of data-service.

## Error propagation implemented by the application

A downstream error can become visible at multiple upstream components because each service returns a result to its caller.

For example, when a request to data-service cannot be completed, processing-service can return an error to api-gateway, and api-gateway can return an error to the synthetic client.

This application behaviour produces the following possible propagation direction:

```text
data-service-side result
        -> processing-service response
        -> api-gateway response
        -> traffic-generator observation
```

The presence of an upstream error therefore identifies where an error was observed, not necessarily where it originated.

## Local resource scope

CPU and memory measurements are collected per monitored container. A resource value for one service belongs to that service's container scope.

The architecture does not define a direct dependency between worker-service and the Notes request chain. Any simultaneous behaviour between worker-service and Notes services must therefore be established from runtime evidence rather than from a static application dependency.

## Network-link scope

The three critical request-path links are:

```text
traffic-generator  -> api-gateway
api-gateway        -> processing-service
processing-service -> data-service
```

Each NETLAT detector is SINGLE_ENTITY and represents only its configured source/link.

A network observation on one link does not automatically describe either of the other links.

## Availability, reachability and application behaviour

These are separate properties:

- a container can be running while its application returns an error;
- a service can be reachable at the network layer while an application request fails;
- a service process can be running while a downstream dependency is unavailable;
- telemetry can be missing because the service stopped or because the observability path failed.

The architecture does not collapse these properties into one health state.

## Request-level correlation

`X-Request-ID` is propagated along the application path. For one operation, the same `request_id` can connect:

- the traffic-generator action;
- api-gateway request and downstream timing;
- processing-service downstream timing;
- data-service persistence activity.

Timestamps provide temporal ordering, while `request_id` provides request-level identity.

## Static model boundary

This document describes dependency direction and possible propagation paths implemented by the system. It does not state which component is faulty during a live incident. Causal conclusions must be produced from runtime observations by the agents.
