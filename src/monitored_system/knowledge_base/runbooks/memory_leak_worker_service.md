---
kb_id: monitored-system.runbook.memory-leak-worker-service
version: 1
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [worker-service]
incident_types: [memory, resource-exhaustion]
source_files: [src/monitored_system/infrastructure/scenarios/memory-leak/scenario.yaml]
---

# Runbook: Memory Leak on worker-service

## Ground-truth controlled fault

The scenario runs a process inside `worker-service` that progressively allocates and retains memory. The growth is bounded. Default configuration allocates up to 512 MB in 32 MB steps every 2 seconds; the configured maximum is 2048 MB.

## Expected evidence

Primary metric:

```text
docker_container_mem.usage_percent
```

Expected detector:

```text
RAM-worker-service
```

The detector is SINGLE_ENTITY and represents only `worker-service`.

Expected behaviour is progressive memory growth followed by a sustained elevated level once the configured allocation is reached.

## Diagnostic interpretation

A monotonic or step-like rise in worker-service container memory, followed by retained high usage, supports a memory-retention hypothesis. Because worker-service is independent from the Notes HTTP request path, the anomaly can exist without API failures.

Do not claim a cascading application outage unless additional evidence shows shared host pressure or failures in other services.

## Useful checks

- Query recent `docker_container_mem.usage_percent` for worker-service.
- Compare the first and last values in the investigation window.
- Verify that the container is still running.
- Inspect worker logs only as supporting context; synthetic application log messages are not the memory-allocation process itself.

## Recovery knowledge

Terminate only the injected allocation process through the scenario stop mechanism.

## Validation after recovery

Container memory usage should decrease towards the normal baseline after the injected process exits. The worker container itself should remain available.
