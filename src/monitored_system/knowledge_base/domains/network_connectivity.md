---
kb_id: monitored-system.domain.network-connectivity
version: 3
domain: monitored_system
document_type: domain-reference
roles: [network_engineer, technical_lead, application_engineer]
domains: [network, connectivity, latency, probes, ports]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [network-latency, availability, downstream-failure]
source_files:
  [
    src/monitored_system/docker-compose.yml,
    src/monitored_system/infrastructure/telegraf.conf,
    src/infrastructure/opensearch/init/create-anomaly-detectors.sh,
  ]
---

# Network Connectivity Reference

## Application links

The normal Notes request path contains three critical network links:

```text
traffic-generator  -> api-gateway:5000
api-gateway        -> processing-service:8000
processing-service -> data-service:8000
```

Each link has a distinct source, destination and application dependency direction.

## ICMP observations

The `ping` measurement provides ICMP-oriented information. Useful fields include:

- `average_response_ms`;
- `percentile50_ms`;
- `percentile95_ms`;
- `percentile99_ms`;
- `percent_packet_loss`;
- `result_code`.

ICMP reachability and RTT describe network-layer behaviour. They do not execute the service's application request path.

## Transport-level network observations

The `network_transport_latency` measurement is produced by the configured Telegraf `net_response` probe.

Important fields are:

```text
network_transport_latency.response_time
network_transport_latency.result_code
```

The probe uses TCP and intentionally does not configure `send` or `expect`. Its `response_time` therefore represents TCP connection-establishment latency to the configured destination service, rather than application processing time.

The source service is represented by the OpenSearch index (`metrics-<source>-*`) and the destination is stored in the Telegraf tag `tag.network_target`. NETLAT investigations must preserve both endpoints of the detector path when retrieving this telemetry.

## Application-service latency observations

Application-level response timing is collected separately as:

```text
application_service_latency.response_time
application_service_latency.result_code
```

This measurement is produced by the configured Telegraf `http_response` probe. It executes an HTTP health request against the target service and can therefore include application and downstream dependency time that is outside pure TCP connection establishment.

The configured application probe paths are:

```text
traffic-generator  -> api-gateway          /notes/new
api-gateway        -> processing-service   /docs
processing-service -> data-service         /docs
```

These observability requests are not equivalent to every user operation handled by the Notes Platform.

## SINGLE_ENTITY detector scope

The configured network transport-latency detectors are:

- `NETLAT-traffic-generator-api-gateway`;
- `NETLAT-api-gateway-processing-service`;
- `NETLAT-processing-service-data-service`.

Each detector is SINGLE_ENTITY and uses telemetry from its configured source/link. A result from one detector must not be interpreted as a measurement of another link.

Application-latency detectors use the separate `application_service_latency` measurement and must not be conflated with NETLAT transport latency.

## Ports and service reachability

The main application ports are:

- `api-gateway`: container port 5000;
- `processing-service`: container port 8000;
- `data-service`: container port 8000.

A successful TCP connection indicates that the destination port accepted the connection. It does not by itself confirm correct business behaviour of the complete application.

Likewise, a failed application request can reflect transport, destination-service or downstream application conditions. These are distinct technical possibilities and require live evidence to distinguish.

## Timing boundaries

The system exposes multiple latency values:

- ICMP RTT from `ping`;
- TCP connection-establishment time from `network_transport_latency`;
- HTTP application-service response time from `application_service_latency`;
- downstream application latency from service logs;
- end-to-end user-visible latency from traffic-generator.

Because these values observe different layers and scopes, differences between them are expected and must be interpreted in context by the agent. Similar numerical values do not mean that they measure the same operation.

## Network knowledge boundary

This document defines topology, ports, probe behaviour and measurement semantics. It does not define a symptom pattern that should be mapped directly to a network root cause. Live telemetry remains the source of truth for runtime diagnosis.
