# Monitored System Knowledge Base

This directory contains stable technical knowledge about the Notes Platform monitored by AgenticProactiveMonitor. The documents are intended for ingestion into Qdrant and retrieval through RAG while the agents analyse live incidents.

The knowledge base must describe the system and the technical meaning of its observable data. It must not provide ready-made incident diagnoses, scenario answers or rules that map a set of symptoms directly to a root cause.

## Virtual technical team

Knowledge retrieval is aligned with five real operational roles:

- `technical_lead`: cross-domain architecture, dependencies, impact boundaries and shared system context;
- `system_engineer`: Linux/container runtime, CPU, memory, disk, processes and service state;
- `network_engineer`: connectivity, active probes, RTT, service latency, ports and network paths;
- `application_engineer`: service health, dependency chain, request flow, application timing and operational logs;
- `software_developer`: expected software behaviour, API semantics, validation, error handling, persistence behaviour and code-level context.

These names describe professional perspectives used for retrieval. The knowledge base does not describe agent prompts, BDI state, ReAct implementation, confidence thresholds or collaboration workflow.

## Knowledge policy: descriptive, not prescriptive

The knowledge base contains:

- system architecture and runtime dependencies;
- service responsibilities, ports, endpoints and persistence;
- container and runtime characteristics;
- telemetry sources and metric semantics;
- network links and probe semantics;
- log fields and event meanings;
- OpenSearch detector definitions and entity scope;
- expected application behaviour and error propagation implemented by the software;
- general technical concepts needed to interpret runtime observations.

The knowledge base does not contain:

- incident-specific diagnosis guides;
- symptom-to-root-cause rules;
- pre-written hypotheses for evaluation scenarios;
- test-suite implementation or results;
- controlled fault-injection scripts or parameters;
- scenario ground truth or expected detector outcomes;
- agent-runtime implementation details, workflow limits, confidence thresholds or remediation allowlists.

Files under `src/monitored_system/infrastructure/tests/` and `src/monitored_system/infrastructure/scenarios/` are evaluation/support material and must not be ingested into the monitored-system RAG collection.

The agents must formulate hypotheses from live evidence. Retrieved knowledge provides context for reasoning but is never evidence that a particular fault is currently present.

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
└── services/
    ├── traffic_generator.md
    ├── api_gateway.md
    ├── processing_service.md
    ├── data_service.md
    └── worker_service.md
```

`README.md` and `manifest.yaml` describe ingestion policy and structure. The technical Markdown documents listed by `manifest.yaml` are the content intended for embedding.

## Knowledge layers

`shared/` contains cross-role facts about the system: architecture, dependencies, telemetry and observable relationships.

`domains/` contains general professional knowledge anchored to the monitored environment. These documents explain what runtime, network, application and software observations mean, but they do not tell an agent which diagnosis to select.

`services/` contains one factual reference per monitored service. Keeping services separate improves chunk quality and allows Qdrant filtering by service without retrieving unrelated service descriptions.

## Role-aware RAG

Each ingestible Markdown document starts with YAML-style metadata. The `roles` field identifies the professional roles for which the document is especially relevant. `domains` and `services` provide additional retrieval scope.

For example, a Network Engineer investigating `api-gateway` can privilege network topology, port and probe documentation, while a Software Developer working on the same service can privilege API, validation and error-handling knowledge.

Role filtering should guide retrieval, not create isolated knowledge silos. Specialists can retrieve broader cross-domain context when the incident requires it.

## Recommended Qdrant payload

For each chunk, preserve at least:

- `kb_id`;
- `domain`;
- `document_type`;
- `roles`;
- `domains`;
- `services`;
- `source_files`;
- `version`.

`incident_types` may be retained as a broad retrieval hint where already present, but it must not encode scenario ground truth and should not be treated as a diagnosis label.

## Retrieval principle

Live OpenSearch metrics, logs, tool observations and runtime state are the source of truth for an active incident. Qdrant provides static knowledge about what the system is, how it is connected and what each observable field means.

The intended reasoning pattern is:

```text
live anomaly or incident
        ↓
agent reasons about missing information
        ↓
agent uses tools and/or retrieves system knowledge
        ↓
new observations and technical context
        ↓
agent updates its hypotheses
        ↓
additional actions when needed
        ↓
agent-produced diagnosis
```

The diagnosis is therefore produced by the agent from current evidence. It is not retrieved from the knowledge base.
