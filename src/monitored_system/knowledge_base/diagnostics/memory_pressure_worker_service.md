---
kb_id: monitored-system.diagnostic.memory-pressure-worker-service
version: 2
domain: monitored_system
document_type: diagnostic-guide
roles: [system_engineer, technical_lead]
domains: [memory, containers, runtime, resource-exhaustion]
services: [worker-service]
incident_types: [memory, resource-exhaustion]
source_files:
  [
    src/monitored_system/infrastructure/telegraf.conf,
    src/monitored_system/docker-compose.yml,
    src/monitored_system/infrastructure/entrypoint.sh,
  ]
---

# Memory Pressure on worker-service

## Diagnostic pattern

A memory-related incident on `worker-service` should be evaluated using:

```text
docker_container_mem.usage_percent
```

The corresponding OpenSearch detector is:

```text
RAM-worker-service
```

The detector is SINGLE_ENTITY and represents only `worker-service`.

## Evidence that supports memory pressure

A local memory-pressure or retained-memory hypothesis becomes stronger when:

- worker-service container memory increases relative to its recent baseline;
- memory remains elevated instead of returning quickly to the previous level;
- the container remains running while the elevated usage persists;
- no stronger explanation is provided by missing telemetry or container restart activity.

A progressive increase over time is especially relevant when distinguishing retained memory from a short-lived allocation peak.

## System impact

`worker-service` is independent from the Notes HTTP request path. High memory on this service does not by itself prove impact on `api-gateway`, `processing-service` or `data-service`.

Broader impact should only be claimed when live evidence shows effects outside the worker, such as shared runtime pressure, additional service anomalies or request failures occurring in the same period.

## Useful diagnostic checks

- inspect recent `docker_container_mem.usage_percent` values for worker-service;
- compare the beginning and end of the investigation window;
- check whether the container remained continuously available;
- inspect worker logs for restart or abnormal runtime evidence;
- check other services only when there is evidence of wider impact.

## Diagnosis rule

A memory diagnosis must be based on the observed memory trend and current runtime evidence. This document describes a possible failure pattern and does not identify the cause of a specific incident.
