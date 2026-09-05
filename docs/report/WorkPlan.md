# Work Plan

The project is developed incrementally. Each phase produces a component that can be tested independently before it is connected to the complete agentic workflow.

## Phase 1 — Monitoring infrastructure

**Status: completed and operational.**

The first phase established the observability stack and the separation between the monitoring infrastructure and the monitored workload.

Implemented activities:

- OpenSearch and OpenSearch Dashboards deployment;
- Qdrant deployment for the vector knowledge base;
- Open WebUI integration for manual RAG experiments;
- Prosody/XMPP deployment for agent communication;
- native Ollama integration from the Windows host;
- shared Docker observability network between the infrastructure and monitored system.

## Phase 2 — Monitored distributed application

**Status: completed and operational.**

A realistic but controlled Notes Platform was implemented to provide a system on which faults can be reproduced and diagnosed.

The current workload includes:

- a Flask API Gateway and web interface;
- a FastAPI Processing Service;
- a FastAPI Data Service with SQLite persistence;
- a background Worker Service;
- a Traffic Generator that produces real HTTP requests every few seconds.

Requests carry an `X-Request-ID`, allowing logs from different services to be correlated across the request path.

## Phase 3 — Telemetry and anomaly detection

**Status: completed for the current experimental scope.**

Every monitored container runs Telegraf and Fluent Bit. Metrics and logs are written to service-specific OpenSearch indices.

The current detector set contains **13 SINGLE_ENTITY detectors**:

- 5 CPU detectors;
- 5 RAM detectors;
- 3 network-service-latency detectors.

The detector bootstrap waits for a configurable amount of historical data before the detector is created and enabled.

## Phase 4 — Controlled fault scenarios and experiments

**Status: implemented.**

The monitored system includes repeatable PowerShell scenarios for:

- CPU saturation on `processing-service`;
- progressive memory pressure on `worker-service`;
- real network latency between `api-gateway` and `processing-service` using Linux `tc/netem`;
- application-level processing latency;
- `data-service` unavailability.

A dedicated PowerShell test suite checks the base state, telemetry, detector configuration, scenario execution, anomaly results, and recovery. Experimental results can be written to JSON and detector-confidence history CSV files.

## Phase 5 — Knowledge base and RAG

**Status: implemented at infrastructure and content level.**

A monitored-system knowledge base has been created for Qdrant. It contains stable technical knowledge rather than test answers or scenario ground truth.

Knowledge is organised around the five target professional roles and contains:

- system architecture;
- observability semantics;
- service responsibilities;
- runtime and Linux/container knowledge;
- network knowledge;
- application behaviour;
- diagnostic patterns.

The MCP Server already exposes `search_knowledge()` to embed a query with Ollama and retrieve relevant Qdrant chunks.

## Phase 6 — Controlled diagnostic tools

**Status: implemented and tested.**

The MCP Server exposes read-only tools for:

- OpenSearch metrics;
- OpenSearch logs and full-text log search;
- container processes;
- container runtime statistics;
- disk usage;
- network connections;
- Qdrant knowledge retrieval.

The server deliberately does not expose a generic remote shell. Docker inspection is restricted to the five monitored containers.

## Phase 7 — Operator dashboard

**Status: implemented as a working prototype.**

The Flask dashboard provides:

- incident overview and detail pages;
- infrastructure health information;
- diagnosis and remediation fields;
- the five-role virtual technical team;
- per-agent activity events;
- OpenSearch persistence for incidents and agent events.

The dashboard is separated from the agent control loop and acts as an operator-facing observability component.

## Phase 8 — Hybrid multi-agent backend

**Status: current development focus.**

The next step is the autonomous reasoning backend. The target design combines:

- SPADE agents communicating through XMPP;
- explicit BDI state for beliefs, desires, and intentions;
- a real ReAct execution loop for tool use;
- dynamic specialist delegation by the Technical Lead;
- MCP tools for live evidence collection;
- Qdrant RAG for domain knowledge;
- local Ollama models for reasoning and tool selection.

The intended runtime must avoid a fixed specialist pipeline. The Technical Lead should select the specialists that are useful for the current incident and revise the investigation when new evidence changes the hypothesis.

## Phase 9 — Final evaluation

**Status: planned after backend integration.**

The final evaluation will use the controlled scenarios to measure the complete pipeline from anomaly to diagnosis. The experimental focus will include:

- detection behaviour;
- diagnostic correctness;
- evidence quality;
- confidence and uncertainty;
- time required for investigation;
- usefulness of RAG;
- contribution of specialist roles;
- explainability of the final diagnosis and remediation proposal.
