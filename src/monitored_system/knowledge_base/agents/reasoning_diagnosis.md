---
kb_id: monitored-system.agent.reasoning
version: 1
domain: monitored_system
document_type: agent-context
agents: [reasoning]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/agentic_system/simple/services.py, src/monitored_system/infrastructure/scenarios/README.md]
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

High `processing-service` container CPU with normal network RTT and no data-service reachability errors supports a local CPU-load hypothesis. Downstream errors without abnormal CPU weaken that hypothesis.

### Memory exhaustion

Progressive and retained growth in `worker-service` container memory supports a memory-leak or retained-allocation hypothesis. The expected controlled scenario is bounded and does not require an HTTP failure.

### Network latency vs application latency

Network latency should increase packet/TCP timing on the affected link. Application latency injected inside processing-service should increase request duration while ICMP RTT remains near baseline. The `fault_delay_applied` event is strong evidence for the controlled application-delay scenario.

### Service unavailable

If data-service is stopped, processing-service should report connection failure, api-gateway can return 503, and traffic-generator can report failed actions. Missing fresh data-service telemetry plus stopped-container state strongly supports service unavailability.

## Hypothesis discipline

For each hypothesis:

- identify the component;
- name the suspected failure mode;
- connect evidence to the causal mechanism;
- state contradictory or missing evidence;
- assign confidence proportionally to evidence quality;
- prefer a narrower cause over a broad cascading-failure label when a direct cause is supported.

Do not infer a component name that is not present in the monitored topology. Do not invent dependencies. When evidence is ambiguous, request a concrete metric, log or container-state check.
