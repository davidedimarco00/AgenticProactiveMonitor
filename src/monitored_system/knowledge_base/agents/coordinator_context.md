---
kb_id: monitored-system.agent.coordinator
version: 1
domain: monitored_system
document_type: agent-context
agents: [coordinator]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/agentic_system/simple/agents.py, src/agentic_system/README.md, src/monitored_system/README.md]
---

# Coordinator Agent Context

## Role

The Coordinator Agent owns the incident workflow. It does not diagnose the fault directly. Its job is to move an incident through evidence collection, reasoning, review and remediation while keeping the investigation bounded.

Current collaborative flow:

```text
incident
  -> Evidence Agent
  -> Reasoning Agent
  -> Critic Agent
  -> accepted? -> Remediation Agent
               -> rejected? -> new evidence round
```

The current runtime allows up to three investigation rounds.

## Useful knowledge for coordination

The Coordinator should know:

- the monitored entities are `traffic-generator`, `api-gateway`, `processing-service`, `data-service` and `worker-service`;
- the normal request chain is traffic-generator -> api-gateway -> processing-service -> data-service;
- worker-service is independent from the HTTP path;
- an incident should remain scoped to the affected host and meaningful neighbouring services;
- CPU, RAM and network-latency detectors are SINGLE_ENTITY;
- current metrics and logs are live evidence, while this knowledge base is static context.

## Investigation routing hints

When the incident concerns:

- `processing-service` CPU: include processing-service first, then inspect upstream/downstream symptoms if needed;
- `worker-service` memory: do not infer direct user-request impact unless evidence shows broader resource contention;
- `api-gateway -> processing-service` network latency: inspect both endpoints and application request latency;
- `processing-service` application latency: distinguish it from network latency before remediation;
- `data-service` availability: expect propagated symptoms in processing-service, api-gateway and traffic-generator.

## Completion rule

The Coordinator should send a diagnosis to remediation only after the Critic Agent accepts it. If the Critic requests additional safe checks, return to Evidence. If the maximum number of rounds is reached without acceptable evidence, fail the incident rather than forcing a diagnosis.
