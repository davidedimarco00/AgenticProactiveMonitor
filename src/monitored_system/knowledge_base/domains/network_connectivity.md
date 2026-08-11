---
kb_id: monitored-system.domain.network-connectivity
version: 1
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

The normal request path contains three critical network links:

```text
traffic-generator  -> api-gateway:5000
api-gateway        -> processing-service:8000
processing-service -> data-service:8000
```

Network evidence must always be interpreted for the specific source and target involved in the incident.

## Active network evidence

Each critical source collects two complementary forms of network evidence.

Raw ICMP evidence is stored under the `ping` measurement. Useful fields include:

- `average_response_ms`
- `percentile50_ms`
- `percentile95_ms`
- `percentile99_ms`
- `percent_packet_loss`
- `result_code`

Service-level network evidence is stored under:

```text
measurement_name = network_service_latency
field            = network_service_latency.response_time
```

The corresponding `network_service_latency.result_code` provides probe outcome information.

## Probe targets

The three links used by network-latency detectors are:

```text
traffic-generator  -> api-gateway          /notes/new
api-gateway        -> processing-service   /docs
processing-service -> data-service         /docs
```

The service-level probe opens the configured TCP connection and waits for the expected HTTP response. It therefore provides a different signal from raw ICMP RTT.

## Detector semantics

Network-latency detectors are SINGLE_ENTITY. The configured detectors are:

- `NETLAT-traffic-generator-api-gateway`
- `NETLAT-api-gateway-processing-service`
- `NETLAT-processing-service-data-service`

Each detector uses the metrics index of its source service and represents only that configured source/link.

## Network versus application symptoms

A network-path problem becomes more plausible when several signals move together:

- `network_service_latency.response_time` increases;
- raw ICMP RTT increases for the same target;
- application request latency also increases after the network signals change.

An application-side explanation becomes more plausible when user-visible latency rises while raw ICMP RTT and the service-level network probe remain near baseline.

## Connectivity versus service failure

A failed service-level probe can mean that the target is unreachable or is not returning the expected service response. It should therefore be correlated with:

- ICMP reachability;
- application logs on the source and target;
- target service state;
- request status codes;
- fresh target telemetry.

A failed probe alone does not identify whether the root cause is the network path or the destination service.

## Diagnostic principles

When investigating connectivity or latency:

- identify the exact source and target first;
- keep each SINGLE_ENTITY detector separate;
- compare service-level latency with independent ICMP evidence;
- use application timing only as supporting impact evidence;
- inspect `result_code` when reachability is uncertain;
- avoid calling an application slowdown a network problem when network probes remain normal;
- avoid calling a target service down only because an upstream request failed.
