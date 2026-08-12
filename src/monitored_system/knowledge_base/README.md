# Monitored System Knowledge Base

Qdrant collection: `monitored-system`

This directory contains stable technical documentation about the concrete Notes Platform monitored by AgenticProactiveMonitor. Every specialist agent may retrieve from this collection.

The collection answers questions such as what a service does, which dependency it calls, which port it uses, what a log field means and how telemetry is produced. It must not provide a ready-made incident diagnosis.

## Separation from professional knowledge

The project uses two knowledge levels:

```text
monitored-system
    = knowledge about this concrete monitored system

role-specific collections
    = general professional/domain knowledge
```

The role-specific knowledge bases live under `src/knowledge_bases/`. For example, Linux operating-system knowledge for the System Engineer is stored in `kb-system-engineer-linux` instead of being mixed with the Notes Platform documentation.

During an incident a specialist can therefore combine:

```text
shared monitored-system context
        +
role-specific professional knowledge
        +
live metrics/logs/tool observations
        -> agent reasoning
        -> agent-produced hypotheses and diagnosis
```

## Virtual technical team

The shared collection is available to all five operational roles:

- `technical_lead`;
- `system_engineer`;
- `network_engineer`;
- `application_engineer`;
- `software_developer`.

Role metadata inside documents is only a relevance hint. It does not prevent another specialist from retrieving system documentation when cross-domain context is needed.

## Knowledge policy: descriptive, not prescriptive

The monitored-system collection contains:

- system architecture and runtime dependencies;
- service responsibilities, ports, endpoints and persistence;
- deployment/container characteristics of this system;
- telemetry sources and metric semantics used by this system;
- network links and configured probe semantics;
- log fields and event meanings implemented by the application;
- OpenSearch detector definitions and entity scope;
- expected application behaviour and implemented error propagation.

The monitored-system collection does not contain:

- general Linux manuals that belong to the System Engineer collection;
- general network or software manuals that belong to specialist collections;
- incident-specific diagnosis guides;
- symptom-to-root-cause rules;
- pre-written hypotheses for evaluation scenarios;
- test-suite implementation or results;
- controlled fault-injection scripts or parameters;
- scenario ground truth or expected detector outcomes;
- agent-runtime implementation details, confidence thresholds or remediation allowlists.

Files under `src/monitored_system/infrastructure/tests/` and `src/monitored_system/infrastructure/scenarios/` are evaluation/support material and must not be ingested into Qdrant knowledge collections.

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

`README.md` and `manifest.yaml` define ingestion policy and structure. The technical Markdown documents listed in the manifest are the documents intended for embedding in `monitored-system`.

## Knowledge layers inside the collection

`shared/` contains cross-role facts about this system: architecture, dependency direction and telemetry relationships.

`domains/` reorganizes system-specific information by operational viewpoint. These files remain about the Notes Platform and its telemetry; general professional manuals belong to the separate role collections.

`services/` contains one factual reference per monitored service. Keeping services separate improves chunk quality and allows filtering by service without retrieving unrelated service descriptions.

## Recommended Qdrant payload

For each chunk, preserve at least:

- `kb_id`;
- `collection`;
- `domain`;
- `document_type`;
- `roles`;
- `domains`;
- `services`;
- `source_files`;
- `version`.

Role and service metadata can guide retrieval, but all agents are allowed to use the shared `monitored-system` collection.

## Retrieval principle

Live OpenSearch metrics, logs, runtime state and other tool observations are the source of truth for an active incident. Qdrant provides static knowledge about what the monitored system is and what its observable data means.

The intended pattern is:

```text
live anomaly or incident
        ↓
agent reasons about missing information
        ↓
agent queries tools and/or monitored-system knowledge
        ↓
agent may query its professional collection
        ↓
new observations and technical context
        ↓
agent updates hypotheses
        ↓
additional actions or collaboration when needed
        ↓
agent-produced diagnosis
```

The diagnosis is produced by the agent. It is never retrieved as an answer from this collection.
