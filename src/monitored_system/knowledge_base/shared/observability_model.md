---
kb_id: monitored-system.shared.observability-model
version: 2
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

Every record carries system context such as `project`, `environment`, `host_id`, `machine_role` and the collector identity.

## Metric collection

Telegraf collects telemetry every 10 seconds. Important measurements include:

- `docker_container_cpu`: per-container CPU telemetry. The anomaly-relevant field is `usage_percent`.
- `docker_container_mem`: per-container memory telemetry. The anomaly-relevant field is `usage_percent`.
- `ping`: raw ICMP RTT and packet-loss telemetry towards the configured dependency. Important fields include `average_response_ms`, `percentile50_ms`, `percentile95_ms`, `percentile99_ms`, `percent_packet_loss` and `result_code`.
- `network_service_latency`: end-to-end network/service probe produced by Telegraf `net_response` with `name_override`. It opens the configured TCP connection, sends a small HTTP GET request and waits for a valid `200 OK` response. Important fields are `response_time` and `result_code`.
- `net`: interface bytes, packets, errors and drops.
- `disk`, `diskio`, `system`, `swap`, `processes`, `kernel`: general diagnostic context.

System CPU and memory measurements exist, but CPU/RAM anomaly detection is based on Docker container metrics because multiple containers share the same Docker host kernel view.

## Active network probes

Each monitored service has one configured network target. The three critical application links used by network-latency detection are:

```text
traffic-generator  -> api-gateway          probe path /notes/new
api-gateway        -> processing-service   probe path /docs
processing-service -> data-service         probe path /docs
```

The raw `ping` measurement is retained as an independent ICMP reference. The OpenSearch NETLAT detectors do not use `ping` as their primary feature. They use:

```text
measurement_name = network_service_latency
field            = network_service_latency.response_time
```

The HTTP probe helps measure the complete network/service response path instead of only TCP connection establishment. `result_code` is useful supporting evidence when the target cannot be reached or the expected response is not received.

## Distinguishing network and application latency

For a real network-delay fault on a critical link, both raw ICMP RTT and `network_service_latency.response_time` are expected to increase, with user-visible request latency often increasing as a consequence.

For the controlled application-delay fault inside `processing-service`, note-processing request duration increases while the independent ICMP probe and the `/docs` network-service probe should remain close to their normal baselines. The application log event `fault_delay_applied` is strong evidence for that internal delay.

## SINGLE_ENTITY detector rule

OpenSearch Anomaly Detection detectors for this monitored system must be SINGLE_ENTITY. The configured detector model is:

- one CPU detector per monitored service: 5 detectors;
- one RAM detector per monitored service: 5 detectors;
- one network-latency detector per critical source/link: 3 detectors.

The NETLAT detector names are:

- `NETLAT-traffic-generator-api-gateway`;
- `NETLAT-api-gateway-processing-service`;
- `NETLAT-processing-service-data-service`.

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
- `network_service_latency` anomaly + raw ICMP RTT;
- network probe + request latency;
- missing telemetry + container stopped state;
- resource anomaly + service-specific symptoms.
