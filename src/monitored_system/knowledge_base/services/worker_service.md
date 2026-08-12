---
kb_id: monitored-system.service.worker-service
version: 1
domain: monitored_system
document_type: service-reference
roles: [technical_lead, system_engineer, network_engineer, application_engineer]
domains: [runtime, background-workload, containers, observability, resources]
services: [worker-service]
incident_types: [cpu, memory, resource-exhaustion, availability]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/infrastructure/entrypoint.sh, src/monitored_system/infrastructure/telegraf.conf]
---

# worker-service Service

## Purpose

`worker-service` is an independent background workload. It does not participate in the Notes HTTP request path and does not provide the CRUD API used by `api-gateway`, `processing-service` or `data-service`.

Its main diagnostic boundary is therefore local: a resource anomaly on worker-service must not automatically be interpreted as a Notes request-path failure.

## Runtime model

The service runs the generic synthetic workload provided by the monitored-system entrypoint. It continuously produces:

- system heartbeat records;
- synthetic application events;
- container and system telemetry through Telegraf;
- log forwarding through Fluent Bit.

The machine role is:

```text
background-worker
```

## Synthetic application logs

Synthetic events can include event types such as:

- `request_processed`;
- `background_job_completed`;
- `cache_refreshed`;
- `database_query_executed`;
- `external_service_call`;
- `health_check_completed`;
- `configuration_loaded`;
- `user_session_updated`.

These events model a background application workload. Their messages are synthetic context and should not be interpreted as evidence about the real Notes request chain.

## System heartbeat

The service writes a heartbeat to `/var/log/machine/system.log`. Useful fields include:

- `uptime_seconds`;
- `load_average`;
- `host`;
- `machine_role`.

Fresh heartbeat data supports the conclusion that the monitored container is still producing runtime telemetry, but heartbeat presence alone does not prove that every internal process is healthy.

## Resource evidence

Container CPU and memory should be interpreted using:

```text
docker_container_cpu.usage_percent
docker_container_mem.usage_percent
```

A sustained memory increase is relevant to memory-pressure hypotheses. A short peak should be distinguished from persistent retained usage by comparing multiple observations over time.

## Network context

worker-service also collects diagnostic network probes towards `api-gateway`, but this link is not part of the Notes business request chain and is not one of the three critical NETLAT detector links.

A probe problem observed here should therefore be treated as supporting network evidence, not as direct proof of user-facing application impact.

## Diagnostic interpretation

A worker-service incident should normally stay scoped to worker-service unless independent live evidence shows shared-host or cross-service impact.

Evidence of broader impact could include simultaneous resource degradation in other services, user-facing failures in the Notes path or shared runtime symptoms. Without such evidence, keep the diagnosis local and avoid inferring causal impact on api-gateway, processing-service or data-service.
