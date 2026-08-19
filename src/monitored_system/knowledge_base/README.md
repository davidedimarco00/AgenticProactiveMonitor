# Monitored System Knowledge Base

Qdrant collection: `monitored-system`

This directory contains stable technical documentation about the concrete Notes Platform monitored by AgenticProactiveMonitor. Every specialist agent may retrieve from this same collection.

The purpose of RAG in this project is to provide external knowledge that the LLM cannot reliably know from pretraining: the architecture, configuration, telemetry and implemented behaviour of the monitored system.

General technical knowledge such as Linux commands, TCP/IP concepts, HTTP semantics or common software-engineering concepts is not duplicated in Qdrant. The local LLM is expected to use its pretrained knowledge for those concepts.

## Knowledge model

The agent combines three different sources during incident analysis:

```text
LLM pretrained knowledge
    = general Linux, networking, application and software knowledge

monitored-system RAG
    = knowledge specific to this concrete Notes Platform

live tools
    = current metrics, logs and runtime observations
```

The intended result is:

```text
general technical knowledge
        +
monitored-system documentation
        +
live evidence
        -> agent reasoning
        -> hypotheses
        -> additional tool calls when needed
        -> agent-produced diagnosis
```

## Shared access

The `monitored-system` collection is shared by the complete virtual technical team. There is no role-specific Qdrant collection and no role-specific RAG routing.

Different agents remain specialised because of their role, responsibilities, prompts, BDI state, ReAct reasoning and available tools, not because they receive separate copies of general technical knowledge.

## Knowledge policy: descriptive, not prescriptive

The monitored-system collection contains:

- system architecture and runtime dependencies;
- service responsibilities, ports, endpoints and persistence;
- deployment and container characteristics specific to this system;
- telemetry sources and metric semantics used by this system;
- network links and configured probe semantics;
- log fields and event meanings implemented by the application;
- OpenSearch detector definitions and entity scope;
- expected application behaviour and implemented error propagation.

The monitored-system collection does not contain:

- generic Linux manuals or command references;
- generic networking manuals;
- generic framework or programming documentation already covered by the LLM's general knowledge;
- incident-specific diagnosis guides;
- symptom-to-root-cause rules;
- pre-written hypotheses for evaluation scenarios;
- test-suite implementation or results;
- controlled fault-injection scripts or parameters;
- scenario ground truth or expected detector outcomes;
- agent-runtime implementation details, confidence thresholds or remediation allowlists.

Files under `src/monitored_system/infrastructure/tests/` and `src/monitored_system/infrastructure/scenarios/` are evaluation/support material and must not be ingested into Qdrant.

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

`README.md` and `manifest.yaml` define ingestion policy and structure. Only the technical Markdown documents listed by the manifest are intended for embedding in `monitored-system`.

## Knowledge layers inside the collection

`shared/` contains cross-domain facts about this system: architecture, dependency direction and telemetry relationships.

`domains/` reorganises system-specific information by technical viewpoint. These files remain documentation of the Notes Platform and its concrete observability configuration; they are not generic professional manuals.

`services/` contains one factual reference per monitored service. Keeping services separate improves chunk quality and allows retrieval of service-specific documentation without mixing unrelated components.

## Qdrant payload

For each chunk, preserve factual retrieval metadata such as:

- `kb_id`;
- `collection`;
- `domain`;
- `document_type`;
- `domains`;
- `services`;
- `source_files`;
- `source_path`;
- `version`.

Role labels and incident diagnosis labels are intentionally not required for retrieval.

## Retrieval principle

Live OpenSearch metrics, logs, runtime state and other tool observations are the source of truth for an active incident. Qdrant provides static knowledge about what the monitored system is and what its observable data means.

The diagnosis is produced by the agent. It is never retrieved as an answer from this collection.
