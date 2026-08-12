---
kb_id: monitored-system.domain.network-connectivity
version: 2
domain: monitored_system
document_type: domain-reference
roles: [network_engineer, technical_lead, application_engineer]
domains: [network, connectivity, latency, probes, ports]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [network-latency, availability, downstream-failure]
source_files: [src/monitored_system/docker-compose.yml, src/monitored_system/infrastructure/telegraf.conf, src/infrastructure/opensearch/init/create-anomaly-detectors.sh]
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

## Service-level network observations

The `network_service_latency` measurement is produced by the configured Telegraf `net_response` probe.

Important fields are:

```text
network_service_latency.response_time
network_service_latency.result_code
```

The probe opens the configured TCP connection, sends a small HTTP request and waits for the expected response. It therefore observes a different path from ICMP ping and includes more than network-layer RTT alone.

## Configured probe targets

```text
traffic-generator  -> api-gateway          /notes/new
api-gateway        -> processing-service   /docs
processing-service -> data-service         /docs
```

The probe paths are observability endpoints/requests and are not equivalent to every user operation handled by the Notes Platform.

## SINGLE_ENTITY detector scope

The configured network-latency detectors are:

- `NETLAT-traffic-generator-api-gateway`;
- `NETLAT-api-gateway-processing-service`;
- `NETLAT-processing-service-data-service`.

Each detector is SINGLE_ENTITY and uses telemetry from its configured source/link. A result from one detector must not be interpreted as a measurement of another link.

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
- service-probe response time from `network_service_latency`;
- downstream application latency from service logs;
- end-to-end user-visible latency from traffic-generator.

Because these values observe different layers and scopes, differences between them are expected and must be interpreted in context by the agent.

## Network knowledge boundary

This document defines topology, ports, probe behaviour and measurement semantics. It does not define a symptom pattern that should be mapped directly to a network root cause.
