# Monitored System Knowledge Base

This directory contains stable technical knowledge about the Notes Platform monitored by AgenticProactiveMonitor. The documents are intended for ingestion into Qdrant and retrieval through RAG during incident analysis.

The knowledge base helps a virtual technical team understand the monitored system, interpret live evidence and formulate competing diagnostic hypotheses. It must not contain the answer to an evaluation scenario.

## Virtual technical team

Knowledge retrieval is aligned with five real operational roles:

- `technical_lead`: cross-domain architecture, dependency impact and evidence required to compare specialist hypotheses;
- `system_engineer`: Linux/container runtime, CPU, memory, disk, processes and service state;
- `network_engineer`: connectivity, active probes, RTT, service latency, ports and network-path evidence;
- `application_engineer`: service health, dependency chain, request flow, application timing and operational logs;
- `software_developer`: expected software behaviour, API semantics, validation, error handling, persistence behaviour and code-level symptoms.

These names describe professional perspectives used for retrieval. The knowledge base does not describe agent prompts, BDI state, ReAct implementation or collaboration workflow.

## Scope

The knowledge base contains only stable knowledge that can support diagnosis:

- system architecture and runtime dependencies;
- service responsibilities, ports, endpoints and persistence;
- container and runtime characteristics relevant to troubleshooting;
- telemetry sources and metric semantics;
- network links and active probe semantics;
- log fields and event meanings;
- OpenSearch detector semantics;
- expected application behaviour and error propagation;
- known failure patterns and discriminating evidence;
- causal relations between downstream faults and upstream symptoms.

The following content is intentionally excluded:

- test-suite implementation and results;
- controlled fault-injection scripts and parameters;
- scenario ground truth;
- expected evaluation outcomes used as labels;
- agent-runtime implementation details, workflow limits, confidence thresholds and allowlists.

Files under `src/monitored_system/infrastructure/tests/` and `src/monitored_system/infrastructure/scenarios/` are evaluation/support material and must not be ingested into the monitored-system RAG collection.

## Knowledge structure

```text
knowledge_base/
├── README.md
├── manifest.yaml
├── shared/
│   ├── system_architecture.md
│   ├── dependency_and_impact_model.md
│   └── observability_model.md
├── domains/
│   ├── infrastructure_runtime.md
│   ├── network_connectivity.md
│   ├── application_operations.md
│   └── software_behavior.md
├── services/
│   ├── traffic_generator.md
│   ├── api_gateway.md
│   ├── processing_service.md
│   ├── data_service.md
│   └── worker_service.md
└── diagnostics/
    ├── cpu_saturation_processing_service.md
    ├── memory_pressure_worker_service.md
    ├── network_degradation_api_gateway_processing_service.md
    ├── application_latency_processing_service.md
    └── data_service_unavailability.md
```

## Why services are separate documents

Each monitored service has its own document instead of one monolithic service reference. This is intentional for RAG quality.

When Qdrant filters on `service = processing-service`, the retrieved chunks should contain processing-service knowledge rather than chunks about an unrelated service that happen to come from a document tagged with all service names.

Per-service documents also provide cleaner semantic chunks for:

- service responsibilities;
- expected endpoints and behaviour;
- direct dependencies;
- important log events;
- network probe relationships;
- failure propagation and diagnostic interpretation.

## Shared, domain and diagnostic knowledge

The three knowledge levels have different purposes.

`shared/` describes facts that are useful across professional roles, such as architecture, dependency direction, impact propagation and telemetry semantics.

`domains/` provides specialist-oriented technical knowledge. For example, the network document explains probes and latency interpretation, while the software document explains API contracts and error handling.

`diagnostics/` describes failure patterns and discriminating evidence. These documents do not state that a failure is currently present and do not reveal which fault is used by the evaluation suite.

## Role-aware RAG

Each ingestible Markdown document starts with YAML-style metadata. The `roles` field identifies the professional roles for which the document is especially relevant. The `domains`, `services` and `incident_types` fields provide additional retrieval filters.

A single Qdrant collection can therefore support role-aware retrieval. For example:

```text
Network Engineer
  role = network_engineer
  service = api-gateway
  incident_type = network-latency
```

should privilege network topology, active probe semantics and network diagnostic patterns.

A Software Developer investigating the same service should instead privilege application behaviour, API semantics, validation and error handling.

Role filtering should guide retrieval, not create isolated knowledge silos. Cross-domain documents can be relevant to more than one role, and specialists can retrieve broader knowledge when an incident crosses domain boundaries.

## Recommended Qdrant payload

For each chunk, preserve at least:

- `kb_id`;
- `domain`;
- `document_type`;
- `roles`;
- `domains`;
- `services`;
- `incident_types`;
- `source_files`;
- `version`.

Semantic similarity can then be combined with metadata filters such as role, service and incident type.

## Retrieval principle

The knowledge base is static context. Live OpenSearch metrics, logs and runtime observations remain the source of truth for an active incident.

Retrieved knowledge should help a specialist answer questions such as:

- Which component is upstream or downstream of the affected service?
- Which metric represents the resource or link being investigated?
- Which logs should correlate with the symptom?
- Which evidence distinguishes a network problem from an application problem?
- Could an upstream error be a consequence of a downstream failure?
- Does the observed behaviour match the normal software contract?

Retrieval of a diagnostic document is never evidence that its failure pattern is present. A diagnosis must be supported by current observations from the monitored environment.
