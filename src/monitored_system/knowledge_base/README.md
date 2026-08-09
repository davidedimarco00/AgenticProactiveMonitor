# Monitored System Knowledge Base

This directory contains the operational knowledge of the Notes Platform monitored by AgenticProactiveMonitor. The documents are written for later ingestion into Qdrant and retrieval through RAG.

The knowledge base is intentionally separated from the source code so that agents can retrieve stable descriptions of the system, its telemetry, known failure modes and safe recovery procedures.

## Knowledge groups

```text
knowledge_base/
├── README.md
├── manifest.yaml
├── shared/
│   ├── system_architecture.md
│   └── observability_model.md
├── agents/
│   ├── coordinator_context.md
│   ├── evidence_collection.md
│   ├── reasoning_diagnosis.md
│   ├── critic_validation.md
│   └── remediation_policy.md
├── services/
│   └── service_reference.md
└── runbooks/
    ├── cpu_spike_processing_service.md
    ├── memory_leak_worker_service.md
    ├── network_latency_api_gateway_processing_service.md
    ├── high_application_latency_processing_service.md
    └── data_service_down.md
```

## Agent differentiation

Each document starts with YAML-style metadata. The `agents` field identifies the roles for which the document is most useful:

- `coordinator`: incident scope, topology and workflow context;
- `evidence`: telemetry sources, expected signals and evidence collection;
- `reasoning`: hypotheses, causal relations and fault discrimination;
- `critic`: validation rules, contradictions and minimum evidence requirements;
- `remediation`: safe, bounded and reversible recovery knowledge.

Shared documents can be retrieved by all roles.

## RAG design

The current knowledge-base uploader accepts Markdown and chunks text by words. Therefore, the metadata below is currently embedded as normal text and remains searchable. A later ingestion step can parse the same fields and copy them into Qdrant payloads without changing the documents.

Recommended future Qdrant payload fields are:

- `kb_id`
- `domain`
- `document_type`
- `agents`
- `services`
- `incident_types`
- `source_files`
- `version`

This makes it possible to combine semantic similarity with metadata filters, for example retrieving only `reasoning` documents related to `processing-service` and `latency`.

## Retrieval principle

Agents should use this knowledge as contextual guidance, not as live state. Current metrics, logs and container state remain the source of truth for the active incident. Knowledge-base content explains what signals mean, which dependencies exist, which hypotheses are plausible and which remediation actions are expected to be safe.
