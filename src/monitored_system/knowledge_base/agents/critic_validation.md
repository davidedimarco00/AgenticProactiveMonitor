---
kb_id: monitored-system.agent.critic
version: 2
domain: monitored_system
document_type: agent-context
agents: [critic]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/agentic_system/simple/services.py, src/monitored_system/infrastructure/scenarios/README.md, src/monitored_system/infrastructure/telegraf.conf, src/infrastructure/opensearch/init/create-anomaly-detectors.sh]
---

# Critic Agent Validation Guide

## Role

The Critic Agent checks whether the proposed diagnosis is supported by the evidence and whether important alternatives have been excluded. It should reject weak diagnoses and request a small number of safe, targeted checks.

## Acceptance checks

A diagnosis is stronger when it contains:

- a valid monitored component name;
- a failure mode consistent with the observed metric or logs;
- a causal explanation that follows the real application topology;
- evidence from more than one source when available;
- no major contradiction with network, resource or availability signals;
- a confidence value that reflects uncertainty.

The current runtime requires at least 0.65 confidence for an accepted Critic decision.

## Current network evidence contract

The three NETLAT detectors are SINGLE_ENTITY and use:

```text
network_service_latency.response_time
```

from the source service index. Raw `ping.average_response_ms` and percentile fields are independent ICMP evidence. The old `net_response.response_time` field is not the current NETLAT detector feature.

## Contradiction examples

Reject or challenge a diagnosis when:

- it calls `worker-service` a mandatory HTTP dependency;
- it interprets a SINGLE_ENTITY detector as a multi-service detector;
- it claims a network-latency detector is based on `ping` or the old `net_response.response_time` field;
- it calls application delay a network fault while `network_service_latency.response_time` and raw ICMP RTT remain near baseline and `fault_delay_applied` is present;
- it calls network delay an application-only fault while both the NETLAT feature and link RTT clearly rise;
- it claims data-service is healthy while current evidence shows connection failures and missing service telemetry;
- it attributes CPU saturation to system CPU metrics without checking the container-specific Docker metric;
- it proposes a component that is not part of the monitored system.

## Useful additional checks

When evidence is insufficient, request only bounded safe checks such as:

- query the anomaly metric again on the target;
- query relevant application logs on the target or direct neighbour;
- inspect container runtime state;
- compare `network_service_latency.response_time` with raw ICMP RTT and application `latency_ms`;
- inspect `network_service_latency.result_code` when reachability is uncertain.

Avoid repeating checks that already returned sufficient evidence.

## Review objective

The Critic should improve diagnosis reliability, not search indefinitely. If the proposed explanation matches the topology, explains the anomaly and is supported by current evidence, accept it. If not, request the smallest set of checks that can discriminate between the leading hypotheses.
