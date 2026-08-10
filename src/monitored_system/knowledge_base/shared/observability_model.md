---
kb_id: monitored-system.shared.observability-model
version: 3
domain: monitored_system
document_type: observability
agents: [evidence, reasoning, critic]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/monitored_system/infrastructure/telegraf.conf, src/monitored_system/infrastructure/fluent-bit.conf, src/monitored_system/docker-compose.yml, src/monitored_system/src/common/logging_utils.py, src/infrastructure/opensearch/init/create-anomaly-detectors.sh]
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

- `docker_container_cpu`: per-container CPU telemetry. The anomaly-relevant field is `usage_percent`.
- `docker_container_mem`: per-container memory telemetry. The anomaly-relevant field is `usage_percent`.
- `ping`: raw ICMP RTT and packet-loss telemetry. Important fields include `average_response_ms`, `percentile50_ms`, `percentile95_ms`, `percentile99_ms`, `percent_packet_loss` and `result_code`.
- `network_service_latency`: active end-to-end probe produced by Telegraf `net_response` with `name_override`. It opens the configured TCP connection, sends a small HTTP request and waits for the expected response. Important fields are `response_time` and `result_code`.
- `net`: interface bytes, packets, errors and drops.
- `disk`, `diskio`, `system`, `swap`, `processes`, `kernel`: supporting diagnostic context.

System CPU and memory measurements are available, but CPU and RAM anomaly detection uses container-specific Docker metrics. This avoids treating the Docker host view as if it represented one application container.

## Active network probes

The three request-path links observed by network-latency detectors are:

```text
traffic-generator  -> api-gateway          probe path /notes/new
api-gateway        -> processing-service   probe path /docs
processing-service -> data-service         probe path /docs
```

The raw `ping` measurement is an independent ICMP reference. The NETLAT detector feature is:

```text
measurement_name = network_service_latency
field            = network_service_latency.response_time
```

`network_service_latency.result_code` provides supporting evidence about probe success or failure.

## Network latency versus application latency

Different evidence patterns should be kept separate.

A network-path degradation is more plausible when:

- `network_service_latency.response_time` increases on the affected source/link;
- raw ICMP RTT also increases;
- application request latency rises as a consequence.

An application-processing problem is more plausible when:

- user-visible or downstream request latency increases;
- network-service probes remain close to baseline;
- raw ICMP RTT remains close to baseline;
- application logs contain service-specific warnings or errors that explain the delay.

One signal alone is usually insufficient to distinguish these cases.

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

A detector result must be interpreted only for the entity or source/link represented by that detector.

## Application logs

Application code writes JSON Lines to `/var/log/machine/app.log`. Common fields include:

- `timestamp`
- `host`
- `machine_role`
- `service`
- `event_type`
- `level`
- `message`
- `request_id` when available

Additional fields can include `latency_ms`, `downstream`, `status_code`, `error_type`, `note_id` and other event-specific values.

System heartbeat records are written to `/var/log/machine/system.log` and include uptime and load information.

## Useful event types

Important application events include:

- `http_request_completed`
- `downstream_request_completed`
- `notes_service_unavailable`
- `data_service_unavailable`
- `synthetic_user_action_completed`
- `synthetic_user_action_failed`
- `service_started`
- `service_stopped`

Other application-specific events should be interpreted together with their timestamp, service, request identifier and surrounding evidence.

## Evidence interpretation

Metrics indicate that behaviour changed. Logs help explain where and how it changed. Runtime inspection can confirm service availability. The knowledge base provides the expected system relationships, but it does not describe the current incident state.

Strong diagnosis should combine independent evidence when possible, for example:

- resource anomaly + service-specific logs;
- `network_service_latency` anomaly + raw ICMP RTT;
- network probe + request latency;
- missing telemetry + independent confirmation of service unavailability;
- downstream error + correlated upstream request failure.
