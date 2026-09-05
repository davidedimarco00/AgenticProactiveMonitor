# Work Plan

The project has been developed incrementally. Each phase produced a component that could be tested before being integrated into the complete autonomous incident workflow.

## Phase 1 — Monitoring infrastructure

**Status: completed and operational.**

The first phase established the local observability and support stack:

- OpenSearch and OpenSearch Dashboards;
- Qdrant for vector knowledge storage;
- MongoDB for durable agentic state;
- Open WebUI for manual local-LLM and RAG experiments;
- Prosody/XMPP for agent communication;
- native Ollama access from the Windows host;
- a shared Docker observability network between the infrastructure and monitored system.

## Phase 2 — Monitored distributed application

**Status: completed and operational.**

A controlled Notes Platform was implemented to provide a realistic distributed workload for monitoring and fault injection.

The current workload contains:

- a Flask API Gateway and web interface;
- a FastAPI Processing Service;
- a FastAPI Data Service with SQLite persistence;
- a background Worker Service;
- a Traffic Generator that produces real HTTP operations every few seconds.

Requests carry an `X-Request-ID`, allowing application logs to be correlated along the main request path.

## Phase 3 — Telemetry and anomaly detection

**Status: completed for the current experimental scope.**

Every monitored container runs Telegraf and Fluent Bit. Metrics and logs are written to service-specific OpenSearch indices.

The current anomaly-detection configuration contains **16 SINGLE_ENTITY detectors**:

- 5 CPU detectors;
- 5 RAM detectors;
- 3 network transport-latency detectors;
- 3 application-service-latency detectors.

The detector bootstrap waits for sufficient historical data before creating and starting the detectors. Logs remain diagnostic evidence and are not currently processed by a semantic log-anomaly detector.

## Phase 4 — Controlled fault scenarios and detector experiments

**Status: implemented.**

Repeatable PowerShell scenarios are available for:

- CPU saturation on `processing-service`;
- progressive memory pressure on `worker-service`;
- real network latency between `api-gateway` and `processing-service` using Linux `tc/netem`;
- application-level processing latency;
- `data-service` unavailability.

The monitored-system test suite validates the base state, telemetry, detector configuration, scenario execution, anomaly results, and recovery. Detector misses are recorded together with successful detections so experiments do not consider only positive results.

## Phase 5 — Knowledge base and RAG

**Status: implemented and integrated.**

The monitored-system knowledge base is ingested into Qdrant and contains stable technical knowledge rather than test answers or fault-injection ground truth.

The content covers:

- system architecture and dependencies;
- observability semantics;
- service responsibilities;
- infrastructure/runtime knowledge;
- network connectivity;
- application behaviour;
- software behaviour and diagnostic guidance.

The MCP Server exposes `search_knowledge()` and the specialist ReAct executor performs an initial static grounding step before collecting live evidence.

## Phase 6 — Controlled diagnostic tools

**Status: implemented and tested.**

The MCP Server exposes a read-only diagnostic surface for:

- detector-aligned CPU and memory history;
- recent logs and full-text log search;
- container processes and process details;
- process threads and process trees;
- current runtime CPU, memory, PID and uptime state;
- filesystem and writable-layer usage;
- TCP/UDP socket state;
- service DNS resolution;
- bounded TCP connectivity checks;
- bounded HTTP endpoint checks;
- ICMP reachability;
- Qdrant knowledge retrieval.

Targets and arguments are allow-listed and schema validated. The server does not expose a generic shell.

## Phase 7 — Operator dashboard and persistence

**Status: implemented and integrated.**

The Flask dashboard now consumes the read-only FastAPI contract exposed by the agentic backend. MongoDB is the persistence layer for incidents and structured history; OpenSearch is reserved for observability and anomaly detection.

The operator can inspect:

- waiting and processed anomalies;
- incident lifecycle and final outcome;
- diagnosis, root cause, confidence and evidence summaries;
- remediation and validation guidance;
- the five-agent team and live activity state;
- structured agent traces and tools used;
- health of the main infrastructure components;
- incident PDF reports.

The dashboard observes the autonomous workflow but does not start or steer investigations.

## Phase 8 — Hybrid multi-agent backend

**Status: implemented and integrated.**

The current production backend includes:

- five SPADE agents with dedicated XMPP identities;
- real AgentSpeak BDI policies for the Technical Lead and specialists;
- dynamic Technical Lead triage and primary-investigator selection;
- durable MongoDB investigation tasks;
- bounded specialist ReAct execution;
- Gemma reasoning and Qwen tool selection;
- MCP tool discovery and schema validation;
- detector-focused incident anchoring;
- initial RAG grounding;
- direct specialist-to-specialist peer assistance when needed;
- Technical Lead post-investigation critic review;
- structured incident, task, anomaly and agent-event persistence.

The runtime deliberately avoids a hard-coded sequence of diagnostic tools. Gemma chooses the evidence family required by the current causal hypothesis, Qwen selects a compatible tool, and Python validates the semantic contract before execution.

## Phase 9 — Fault tolerance and workflow control

**Status: implemented for the current backend scope.**

Anomaly admission uses a durable MongoDB FIFO inbox. The runtime exposes a `FIFO_SINGLE_ACTIVE` processing mode so one anomaly owns the autonomous investigation pipeline at a time. Waiting observations remain persisted across backend restarts.

The backend also includes retry and recovery mechanisms for interrupted anomaly processing, durable tasks, Technical Lead review cycles, and peer-help timeouts. A task failure does not automatically mark an agent as unhealthy; terminal failures can instead move the incident to `OPERATOR_ACTION_REQUIRED`.

## Phase 10 — Final thesis evaluation

**Status: current evaluation focus.**

The complete implementation is now available for controlled evaluation. The next work is therefore focused on experiments rather than on completing the agent architecture.

The evaluation will use the controlled fault scenarios and will compare system behaviour across scenarios and, where useful, across different local reasoning/tool-model configurations. Relevant measures include detection time, diagnostic correctness, localization, root-cause identification, investigation duration, tool usage, number of reasoning steps, evidence quality, and model/token cost where measurable.
