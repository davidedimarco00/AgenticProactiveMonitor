---
kb_id: monitored-system.domain.infrastructure-runtime
version: 2
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

- `traffic-generator`;
- `api-gateway`;
- `processing-service`;
- `data-service`;
- `worker-service`.

Each container runs its application or workload together with Telegraf and Fluent Bit through the monitored-system entrypoint.

## Container resource telemetry

For CPU:

```text
measurement: docker_container_cpu
field:       docker_container_cpu.usage_percent
```

For memory:

```text
measurement: docker_container_mem
field:       docker_container_mem.usage_percent
```

These fields are scoped to the monitored container selected by the Telegraf Docker input.

Host-style `system` CPU and memory measurements are also collected, but they represent a different scope from the per-container Docker measurements.

## Additional runtime measurements

Available supporting measurements include:

- `disk` for filesystem capacity and usage;
- `diskio` for storage I/O counters;
- `processes` for process-state counts;
- `system` for operating-system runtime information;
- `kernel` for kernel-level counters;
- `swap` for swap usage;
- `net` for interface counters.

These measurements describe different aspects of the runtime and should not be treated as interchangeable signals.

## Process and service state

The monitored-system entrypoint starts Telegraf, Fluent Bit and, for application-mode containers, the application process. If one of these supervised processes exits unexpectedly, the entrypoint exits and Docker restart policy can restart the container.

`worker-service` uses the synthetic workload path instead of a real Notes application process.

## System heartbeat

Each container writes system heartbeat records to `/var/log/machine/system.log`. The heartbeat contains fields such as:

- `uptime_seconds`;
- `load_average`;
- `host`;
- `machine_role`.

A new heartbeat means the entrypoint loop produced a record at that time. It does not represent a complete health check of all application dependencies.

## Missing telemetry

Metrics, logs and heartbeats reach OpenSearch through collectors running inside the monitored container. Missing recent data can therefore have more than one technical explanation, including loss of the producing process, container interruption or an observability-path problem.

Static knowledge cannot determine which explanation is present in a live incident.

## Container boundaries

A CPU or memory value for one container belongs to that container. The Notes application request chain and the independent worker-service are separate architectural relationships.

Cross-service effects are runtime observations, not static consequences of every local resource change.
