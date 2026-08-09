---
kb_id: monitored-system.agent.evidence
version: 1
domain: monitored_system
document_type: agent-context
agents: [evidence]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/agentic_system/simple/services.py, src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/infrastructure/fluent-bit.conf]
---

# Evidence Agent Collection Guide

## Role

The Evidence Agent collects facts. It should avoid declaring the root cause. Its output should make later reasoning possible by providing measurements, correlated logs and safe runtime observations.

## Preferred evidence order

1. Read the anomaly context: host, metric, time window and anomaly score.
2. Query the same metric on the affected host.
3. Inspect relevant logs for the same time window.
4. Expand to directly related services only when the topology or symptoms justify it.
5. Run additional safe checks requested by the Critic Agent.

## Metric selection

Use container-specific metrics for resource incidents:

- CPU: `docker_container_cpu.usage_percent`.
- Memory: `docker_container_mem.usage_percent`.

Use network probes for link incidents:

- ICMP RTT: `ping.average_response_ms` and percentile fields.
- TCP connection latency: `net_response.response_time`.
- TCP reachability: `net_response.result_code`.
- Interface context: `net` packets, bytes, errors and drops.

Use general system metrics only as supporting context, not as the primary CPU/RAM signal for a container anomaly.

## Log correlation

Search application logs by `request_id` when investigating propagated request failures. Useful patterns include:

- `notes_service_unavailable` on api-gateway;
- `data_service_unavailable` on processing-service;
- `fault_delay_applied` on processing-service;
- `synthetic_user_action_failed` on traffic-generator;
- HTTP status codes, especially 5xx;
- timeout, refused, failed, retry and error markers.

## Missing telemetry

If a service stops completely, its fresh metrics, application logs and system heartbeats may disappear. Missing telemetry can be evidence of unavailability, but it is not sufficient alone. Confirm container state when possible.

## Evidence quality rules

- Preserve timestamps and host identifiers.
- Keep detector entities separate; detectors are SINGLE_ENTITY.
- Do not merge metrics from different services into one value.
- Do not treat expected synthetic worker logs as proof of an HTTP-path failure.
- Prefer independent evidence types before escalating a strong conclusion.
- Record failed checks as failed evidence rather than silently dropping them.
