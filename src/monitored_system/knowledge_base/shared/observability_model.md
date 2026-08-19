---
kb_id: monitored-system.shared.observability-model
version: 5
domain: monitored_system
document_type: observability
roles:
  [
    technical_lead,
    system_engineer,
    network_engineer,
    application_engineer,
    software_developer,
  ]
domains: [observability, metrics, logs, network, containers]
services:
  [
    traffic-generator,
    api-gateway,
    processing-service,
    data-service,
    worker-service,
  ]
incident_types:
  [cpu, memory, network-latency, application-latency, availability]
source_files:
  [
    src/monitored_system/infrastructure/telegraf.conf,
    src/monitored_system/infrastructure/fluent-bit.conf,
    src/monitored_system/docker-compose.yml,
    src/monitored_system/src/common/logging_utils.py,
    src/infrastructure/opensearch/init/create-anomaly-detectors.sh,
  ]
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

Telemetry contains context such as `project`, `environment`, `host_id`, `machine_role` and collector information.

## Metric collection

Telegraf collects telemetry every 10 seconds. Important measurements include:

- `docker_container_cpu`: per-container CPU telemetry. The anomaly-relevant field is `usage_percent`;
- `docker_container_mem`: per-container memory telemetry. The anomaly-relevant field is `usage_percent`;
- `ping`: ICMP RTT and packet-loss telemetry. Important fields include `average_response_ms`, `percentile50_ms`, `percentile95_ms`, `percentile99_ms`, `percent_packet_loss` and `result_code`;
- `network_service_latency`: active service probe produced by Telegraf `net_response` with `name_override`. It opens the configured TCP connection, sends a small HTTP request and waits for the expected response. Important fields are `response_time` and `result_code`;
- `net`: interface bytes, packets, errors and drops;
- `disk`, `diskio`, `system`, `swap`, `processes`, `kernel`: additional runtime measurements.

System CPU and memory measurements are available, but CPU and RAM anomaly detection uses container-specific Docker metrics. Host-style system measurements and container measurements therefore represent different scopes.

## Active network probes

The three request-path links observed by network-latency detectors are:

```text
traffic-generator  -> api-gateway          probe path /notes/new
api-gateway        -> processing-service   probe path /docs
processing-service -> data-service         probe path /docs
```

The NETLAT detector feature is:

```text
measurement_name = network_service_latency
field            = network_service_latency.response_time
```

`network_service_latency.result_code` records the outcome of the configured service probe.

## Timing semantics

The system exposes several timing measurements with different meanings:

- `ping.*` measures ICMP reachability and round-trip timing;
- `network_service_latency.response_time` measures the configured TCP/HTTP probe response time;
- application `latency_ms` fields measure time spent in application requests or downstream calls;
- traffic-generator `latency_ms` measures the user-facing request duration seen by the synthetic client.

These measurements can change independently because they observe different protocol layers and portions of the request path. Similar numerical values do not mean that they measure the same operation. No single timing field identifies a root cause by itself.

## SINGLE_ENTITY detector rule

OpenSearch Anomaly Detection detectors for this monitored system are SINGLE_ENTITY.

The configured detector model is:

- 5 CPU detectors, one per monitored service;
- 5 RAM detectors, one per monitored service;
- 3 network-latency detectors, one per critical source/link.

The NETLAT detector names are:

- `NETLAT-traffic-generator-api-gateway`;
- `NETLAT-api-gateway-processing-service`;
- `NETLAT-processing-service-data-service`.

A detector result belongs only to the entity or source/link represented by that detector.

## Application logs

Application code writes JSON Lines to `/var/log/machine/app.log`. Common fields include:

- `timestamp`;
- `host`;
- `machine_role`;
- `service`;
- `event_type`;
- `level`;
- `message`;
- `request_id` when available.

Additional fields can include `latency_ms`, `downstream`, `status_code`, `error_type`, `note_id` and event-specific values.

System heartbeat records are written to `/var/log/machine/system.log` and include uptime and load information.

## Important event types

Application events include:

- `http_request_completed`;
- `downstream_request_completed`;
- `notes_service_unavailable`;
- `data_service_unavailable`;
- `synthetic_user_action_completed`;
- `synthetic_user_action_failed`;
- `service_started`;
- `service_stopped`.

The meaning of each event depends on the service that emitted it. For example, `data_service_unavailable` records a failed client operation from processing-service to data-service; it is an observation of that failed call, not a precomputed root-cause label.

## Correlation fields

`request_id` connects application events belonging to the same request across services. `host_id`, `service`, `downstream`, timestamps and network target metadata provide additional scope.

Metrics and logs are observations of the running environment. The knowledge base only defines their structure and semantics; it does not define which combination of observations constitutes a particular incident diagnosis.
