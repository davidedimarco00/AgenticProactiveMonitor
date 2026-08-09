---
kb_id: monitored-system.shared.observability-model
version: 1
domain: monitored_system
document_type: observability
agents: [evidence, reasoning, critic]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/infrastructure/fluent-bit.conf, src/monitored_system/src/common/logging_utils.py]
---

# Observability Model

## OpenSearch index naming

Metrics are written to:

```text
metrics-<host_id>-YYYY.MM.DD
```

Logs are written to:

```text
logs-<host_id>-YYYY.MM.DD
```

Every record carries system context such as `project`, `environment`, `host_id`, `machine_role` and the collector identity.

## Metric collection

Telegraf collects telemetry every 10 seconds. Important measurements include:

- `docker_container_cpu`: per-container CPU telemetry. The anomaly-relevant field is `usage_percent`.
- `docker_container_mem`: per-container memory telemetry. The anomaly-relevant field is `usage_percent`.
- `ping`: ICMP latency and packet-loss telemetry towards the configured dependency. Important fields include `average_response_ms` and percentile values such as p50, p95 and p99.
- `net_response`: TCP reachability and connection response time. Important fields include `response_time` and `result_code`.
- `net`: interface bytes, packets, errors and drops.
- `disk`, `diskio`, `system`, `swap`, `processes`, `kernel`: general diagnostic context.

System CPU and memory measurements exist, but CPU/RAM anomaly detection is based on Docker container metrics because multiple containers share the same Docker host kernel view.

## Active network probes

Each service has one configured network target. Critical application links are:

```text
traffic-generator  -> api-gateway
api-gateway        -> processing-service
processing-service -> data-service
```

For network diagnosis, compare ICMP latency, TCP response time and application request latency. A rise only in application latency is different from a rise in packet-level latency.

## SINGLE_ENTITY detector rule

OpenSearch Anomaly Detection detectors for this monitored system must be SINGLE_ENTITY. The configured detector model is:

- one CPU detector per monitored service;
- one RAM detector per monitored service;
- one network-latency detector per critical source/link.

Do not reason as if one detector represents multiple services or multiple links.

## Application logs

Application code writes JSON Lines to `/var/log/machine/app.log`. Common fields are:

- `timestamp`
- `host`
- `machine_role`
- `service`
- `event_type`
- `level`
- `message`
- `request_id` when available

Additional fields depend on the event, for example `latency_ms`, `downstream`, `status_code`, `error_type`, `note_id`, `delay_ms` or `fault_type`.

System heartbeat records are written to `/var/log/machine/system.log` and include uptime and load information.

## Important event types

Useful application events include:

- `http_request_completed`
- `downstream_request_completed`
- `notes_service_unavailable`
- `data_service_unavailable`
- `fault_delay_applied`
- `synthetic_user_action_completed`
- `synthetic_user_action_failed`
- `service_started`
- `service_stopped`

## Evidence interpretation

Metrics show that something changed. Logs help explain what changed. Container inspection can confirm runtime state. Knowledge-base documents provide expected causal relations, but they must not replace current telemetry.

A strong diagnosis should normally combine at least two independent evidence types when possible, for example:

- anomaly metric + correlated application log;
- network probe + request latency;
- missing telemetry + container stopped state;
- resource anomaly + service-specific symptoms.
