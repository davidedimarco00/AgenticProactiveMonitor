---
kb_id: monitored-system.runbook.cpu-spike-processing-service
version: 1
domain: monitored_system
document_type: incident-runbook
agents: [evidence, reasoning, critic, remediation]
services: [processing-service]
incident_types: [cpu, resource-exhaustion]
source_files: [src/monitored_system/infrastructure/scenarios/cpu-spike/scenario.yaml]
---

# Runbook: CPU Spike on processing-service

## Ground-truth controlled fault

The controlled scenario starts bounded CPU-bound Python workers inside `processing-service`. The Notes application remains running while container-specific CPU consumption increases. Default workers: 4. Maximum configured workers: 8.

## Expected evidence

Primary metric:

```text
docker_container_cpu.usage_percent
```

Expected detector:

```text
CPU-processing-service
```

The detector is SINGLE_ENTITY and represents only `processing-service`.

Supporting signals may include increased system load and slower application operations if CPU contention becomes significant.

## Diagnostic interpretation

Strong evidence for this fault is a clear increase in processing-service container CPU relative to its recent baseline while the service remains reachable. Normal network RTT helps exclude network latency as the primary cause.

Do not use host-level CPU alone as proof, because the monitored containers share the Docker host kernel view.

## Useful checks

- Query recent `docker_container_cpu.usage_percent` for processing-service.
- Inspect processing-service logs for continuing request handling.
- Compare network latency to data-service with the normal baseline.
- Inspect the processing-service container only if runtime confirmation is required.

## Recovery knowledge

The intended recovery is to terminate only the injected CPU workload using the scenario stop mechanism. The normal processing-service application should remain running.

## Validation after recovery

CPU usage should move back towards the learned baseline. Request handling should continue, and no unrelated service should need to be restarted.
