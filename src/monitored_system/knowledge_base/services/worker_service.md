---
kb_id: monitored-system.service.worker-service
version: 2
domain: monitored_system
document_type: service-reference
roles: [technical_lead, system_engineer, network_engineer, application_engineer]
domains: [runtime, background-workload, containers, observability, resources]
services: [worker-service]
incident_types: [cpu, memory, resource-exhaustion, availability]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/infrastructure/entrypoint.sh,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# worker-service Service

## Purpose

`worker-service` is an independent background workload. It does not participate in the Notes HTTP request path and does not provide the CRUD API used by `api-gateway`, `processing-service` or `data-service`.

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

These events model a background application workload. They are not events from the Notes CRUD request chain.

## System heartbeat

The service writes a heartbeat to `/var/log/machine/system.log`. Useful fields include:

- `uptime_seconds`;
- `load_average`;
- `host`;
- `machine_role`.

A heartbeat means that the entrypoint loop produced a new runtime record at that time. It does not report the health of every process or dependency.

## Resource telemetry

Container CPU and memory are available through:

```text
docker_container_cpu.usage_percent
docker_container_mem.usage_percent
```

These values describe the worker-service container. Multiple observations over time are required to distinguish a transient value from a sustained trend.

## Network context

worker-service also collects network probes towards `api-gateway`:

```text
ICMP target:     api-gateway
TCP/HTTP target: api-gateway:5000
probe path:      /notes/new
```

This probe exists as telemetry from worker-service, but the link is not part of the Notes business request path and is not one of the three critical NETLAT detector links.

## System boundary

Because worker-service is outside the Notes request chain, its telemetry describes an independent monitored component. Any relationship between worker-service observations and user-facing Notes behaviour must come from live evidence rather than from a static dependency in the application architecture.
