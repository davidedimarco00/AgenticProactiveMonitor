---
kb_id: monitored-system.shared.system-architecture
version: 1
domain: monitored_system
document_type: architecture
agents: [coordinator, evidence, reasoning, critic, remediation]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/README.md, src/agentic_system/config/topology.yaml]
---

# Notes Platform Architecture

## Purpose

The monitored system is a small distributed Notes Platform used as the external workload for AgenticProactiveMonitor. It is intentionally separate from the agentic infrastructure. The application containers produce telemetry, while the monitoring stack observes them through the shared observability network.

## Runtime application path

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

`worker-service` is an independent synthetic background node. It is not part of the user request path. Its main purpose is to provide a fifth monitored entity and a controlled target for resource faults such as memory exhaustion.

## Service responsibilities

- `traffic-generator`: acts as a synthetic user and continuously creates, reads, updates, deletes and lists notes.
- `api-gateway`: Flask web front end. It receives user traffic and forwards Notes API calls to `processing-service`.
- `processing-service`: FastAPI application layer. It validates note payloads and forwards persistence operations to `data-service`.
- `data-service`: FastAPI persistence layer backed by SQLite in the `notes-data` Docker volume.
- `worker-service`: independent synthetic workload with no HTTP role in the Notes request chain.

## Ports

- `api-gateway`: container port 5000, host port 8080.
- `processing-service`: container port 8000, host port 8002.
- `data-service`: container port 8000, host port 8003.

## Request correlation

A request normally carries an `X-Request-ID`. The identifier is created by the traffic generator or API Gateway and propagated through `api-gateway`, `processing-service` and `data-service`. Agents should use `request_id` to correlate application logs across the request path.

## Networks

Every monitored service joins:

- `monitored-system-net`: application communication;
- `observability-net`: shared network used for integration with the monitoring infrastructure.

Only `api-gateway` receives `NET_ADMIN`, because the controlled network-latency scenario injects delay on its application-network egress path.

## Diagnostic topology note

The current agentic topology registry lists `processing-service` dependencies as `[data-service, worker-service]`. The actual Notes HTTP application path depends on `data-service`; `worker-service` remains operationally independent. Agents should interpret the worker relation as an investigation-scope relation, not as evidence that user requests traverse the worker.

## Normal operating state

During the base scenario:

- all five containers are running;
- the traffic generator performs actions every 2-5 seconds by default;
- no controlled fault scenario is active;
- Telegraf and Fluent Bit run inside every monitored container;
- application and system telemetry continuously reaches OpenSearch.

A diagnosis should compare current evidence against this base state before attributing a fault to a specific component.
