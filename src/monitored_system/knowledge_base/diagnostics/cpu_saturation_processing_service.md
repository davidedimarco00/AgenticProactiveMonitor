---
kb_id: monitored-system.diagnostic.cpu-saturation-processing-service
version: 2
domain: monitored_system
document_type: diagnostic-guide
roles: [system_engineer, application_engineer, software_developer, technical_lead]
domains: [cpu, containers, runtime, application-performance]
services: [processing-service]
incident_types: [cpu, resource-exhaustion]
source_files: [src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/docker-compose.yml, src/monitored_system/src/processing-service/app.py]
---

# CPU Saturation on processing-service

## Diagnostic pattern

A CPU-related incident on `processing-service` should be evaluated using the container-specific metric:

```text
docker_container_cpu.usage_percent
```

The corresponding OpenSearch detector is:

```text
CPU-processing-service
```

The detector is SINGLE_ENTITY and represents only `processing-service`.

## Evidence that supports local CPU pressure

A local CPU-pressure hypothesis becomes stronger when:

- `docker_container_cpu.usage_percent` rises clearly above the recent processing-service baseline;
- the service remains reachable but request handling becomes slower;
- network latency towards `data-service` remains near its normal baseline;
- downstream reachability errors are absent or do not explain the observed slowdown.

## Alternative explanations

High request latency does not prove CPU saturation. Similar user-visible symptoms can be produced by:

- network degradation between services;
- slow or unavailable `data-service`;
- internal application delay;
- a broader runtime or host-level problem.

System-wide CPU measurements are supporting context only. They should not replace the container-specific Docker CPU metric when diagnosing this service.

## Useful diagnostic checks

- inspect recent `docker_container_cpu.usage_percent` values for `processing-service`;
- compare the anomaly period with the preceding baseline;
- inspect processing-service logs for continuing request activity and errors;
- check `processing-service -> data-service` network-service latency;
- check for downstream failure evidence before assigning CPU as the root cause.

## Diagnosis rule

A CPU diagnosis should be based on current resource evidence and correlated service behaviour. Retrieval of this document alone is not evidence that CPU saturation is present.
