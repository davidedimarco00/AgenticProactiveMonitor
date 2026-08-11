---
kb_id: monitored-system.domain.infrastructure-runtime
version: 1
domain: monitored_system
document_type: domain-reference
roles: [system_engineer, technical_lead]
domains: [infrastructure, runtime, containers, cpu, memory, disk, processes]
services:
  [
    traffic-generator,
    api-gateway,
    processing-service,
    data-service,
    worker-service,
  ]
incident_types: [cpu, memory, availability, resource-exhaustion]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/infrastructure/entrypoint.sh,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# Infrastructure and Runtime Reference

## Runtime model

Every monitored component runs in its own Docker container. The five monitored entities are:

- `traffic-generator`
- `api-gateway`
- `processing-service`
- `data-service`
- `worker-service`

Each container exports telemetry through Telegraf and Fluent Bit. Container state and container-specific resource metrics are therefore important when distinguishing a local service problem from a wider host or observability problem.

## Resource evidence

For CPU investigations, use:

```text
measurement: docker_container_cpu
field:       docker_container_cpu.usage_percent
```

For memory investigations, use:

```text
measurement: docker_container_mem
field:       docker_container_mem.usage_percent
```

These metrics represent the monitored container and are preferred over host-style system CPU or memory metrics when the question concerns one specific service.

Supporting runtime measurements include:

- `disk` and `diskio` for filesystem and I/O context;
- `processes` for process-state context;
- `system` and `kernel` for general runtime information;
- `swap` for memory-pressure context;
- `net` for interface counters.

## Service state and missing telemetry

A service that is stopped or unable to run may stop producing fresh metrics, application logs and heartbeat records. Missing telemetry can therefore support an availability hypothesis, but it is not sufficient proof by itself because an observability-path failure can produce a similar symptom.

When telemetry disappears, runtime service state should be checked independently when possible.

## Local versus propagated symptoms

Resource anomalies should initially be interpreted for the service that owns the metric. A CPU or memory anomaly in one container does not automatically prove that another service is unhealthy.

Broader impact should be supported by independent evidence such as:

- increased request latency in dependent services;
- 5xx responses;
- downstream connection failures;
- simultaneous anomalies in other containers;
- loss of service availability.

## worker-service boundary

`worker-service` is independent from the Notes HTTP request path. Resource pressure on this container is normally a local infrastructure/runtime concern unless live evidence demonstrates wider shared-resource impact.

## Diagnostic principles

When investigating infrastructure or runtime problems:

- use container-specific metrics for container-specific conclusions;
- compare the incident period with the recent baseline;
- verify whether the service remained running;
- separate missing telemetry from confirmed service failure;
- correlate resource changes with application symptoms before claiming user impact;
- avoid assigning downstream application errors to infrastructure without supporting runtime evidence.
