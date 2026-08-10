# Monitored System Knowledge Base

This directory contains system knowledge about the Notes Platform monitored by AgenticProactiveMonitor. The documents are intended for ingestion into Qdrant and retrieval through RAG during incident analysis.

The purpose of this knowledge base is to help agents understand the monitored system and interpret live evidence. It must not contain the answer to an evaluation scenario.

## Scope

The knowledge base contains only stable knowledge that can support diagnosis:

- system architecture and runtime dependencies;
- service responsibilities, ports, endpoints and persistence;
- telemetry sources and metric semantics;
- log fields and event meanings;
- OpenSearch detector semantics;
- known failure patterns and discriminating evidence;
- causal relations between downstream faults and upstream symptoms.

The following content is intentionally excluded:

- test-suite implementation and results;
- controlled fault-injection scripts and parameters;
- scenario ground truth;
- expected test detector outcomes used as evaluation labels;
- agent-runtime implementation details, workflow limits, confidence thresholds and allowlists.

In particular, files under `src/monitored_system/infrastructure/tests/` and `src/monitored_system/infrastructure/scenarios/` are evaluation/support material and must not be ingested into the monitored-system RAG collection.

## Knowledge structure

```text
knowledge_base/
├── README.md
├── manifest.yaml
├── shared/
│   ├── system_architecture.md
│   └── observability_model.md
├── services/
│   └── service_reference.md
└── diagnostics/
    ├── cpu_saturation_processing_service.md
    ├── memory_pressure_worker_service.md
    ├── network_degradation_api_gateway_processing_service.md
    ├── application_latency_processing_service.md
    └── data_service_unavailability.md
```

## Agent-specific retrieval

Each Markdown document starts with metadata that identifies which agent roles should retrieve it. The same Qdrant collection can therefore support role-aware RAG without creating separate collections.

Typical retrieval scopes are:

- `coordinator`: architecture, service relationships and incident scope;
- `evidence`: metric semantics, logs and checks that can confirm or reject a hypothesis;
- `reasoning`: causal relations, failure patterns and alternative explanations;
- `critic`: discriminating evidence and contradictions that should invalidate a diagnosis;
- `remediation`: stable system constraints that may later support safe recovery planning.

The role metadata controls retrieval relevance; it does not describe the internal implementation of the agents.

## Recommended Qdrant payload

For each chunk, preserve at least:

- `kb_id`
- `domain`
- `document_type`
- `agents`
- `services`
- `incident_types`
- `source_files`
- `version`

Semantic similarity can then be combined with metadata filters, for example retrieving `reasoning` documents about `processing-service` and `application-latency`.

## Retrieval principle

The knowledge base is static context. Live OpenSearch metrics, logs and runtime observations remain the source of truth for an active incident.

An agent should use retrieved knowledge to answer questions such as:

- Which component is upstream or downstream of the affected service?
- Which metric represents the observed resource or link?
- Which logs should correlate with the symptom?
- Which evidence distinguishes a network problem from an application problem?
- Can an upstream error be a consequence of a downstream failure?

The agent must not infer that a known failure pattern is present only because a similar document was retrieved. A diagnosis must be supported by current evidence.
