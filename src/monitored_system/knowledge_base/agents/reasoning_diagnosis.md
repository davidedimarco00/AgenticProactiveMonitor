---
kb_id: monitored-system.agent.reasoning
version: 2
domain: monitored_system
document_type: agent-context
agents: [reasoning]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/agentic_system/simple/services.py, src/monitored_system/infrastructure/scenarios/README.md, src/monitored_system/infrastructure/telegraf.conf, src/infrastructure/opensearch/init/create-anomaly-detectors.sh]
---

# Reasoning Agent Diagnostic Guide

## Role

The Reasoning Agent converts evidence into ranked hypotheses. It should explain why one hypothesis is more consistent with the evidence than the alternatives and should request more checks when the evidence is insufficient.

## Causal model of the Notes Platform

For user requests, failures can propagate upstream:

```text
data-service fault
    -> processing-service downstream failure
    -> api-gateway failure or HTTP 503
    -> traffic-generator failed user action
```

A fault observed upstream is therefore not automatically an upstream root cause.

`worker-service` is outside the HTTP request path. A worker resource anomaly should normally remain local unless there is separate evidence of host-level contention.

## Main diagnostic distinctions

### CPU saturation vs downstream failure

High `processing-service` container CPU with normal network probes and no data-service reachability errors supports a local CPU-load hypothesis. Downstream errors without abnormal container CPU weaken that hypothesis.

The primary CPU signal is `docker_container_cpu.usage_percent`, not host-level CPU.

### Memory exhaustion

Progressive and retained growth in `worker-service` container memory supports a memory-leak or retained-allocation hypothesis. The expected controlled scenario is bounded and does not require an HTTP failure.

The primary memory signal is `docker_container_mem.usage_percent`.

### Network latency vs application latency

The current NETLAT detectors use `network_service_latency.response_time` from the source service index. Raw `ping.average_response_ms` and its percentiles are independent ICMP references.

A real network-delay fault on a critical link should normally increase both `network_service_latency.response_time` and ICMP RTT, while user-visible request latency may rise as a consequence.

The controlled application-delay scenario is different: `processing-service` sleeps inside the Notes forwarding path. Application `latency_ms` increases and `fault_delay_applied` is emitted, while raw ICMP RTT and the independent `/docs` network-service probe should remain close to baseline.

Do not use the old `net_response.response_time` field as the current NETLAT detector feature.

### Service unavailable

If data-service is stopped, processing-service should report connection failure, api-gateway can return 503, and traffic-generator can report failed actions. Missing fresh data-service telemetry plus stopped-container state strongly supports service unavailability. A failed source-side `network_service_latency` probe can provide additional evidence.

## Hypothesis discipline

For each hypothesis:

- identify the component;
- name the suspected failure mode;
- connect evidence to the causal mechanism;
- state contradictory or missing evidence;
- assign confidence proportionally to evidence quality;
- prefer a narrower cause over a broad cascading-failure label when a direct cause is supported.

Do not infer a component name that is not present in the monitored topology. Do not invent dependencies. When evidence is ambiguous, request a concrete metric, log or container-state check.
