# Requirements Analysis

This section describes the requirements implemented by the current thesis prototype. They cover monitoring, anomaly detection, autonomous diagnosis, persistence, agent collaboration, operator interaction, and quality constraints.

## 1. System boundaries

AgenticProactiveMonitor is divided into two separated runtime environments:

- the **monitored system**, which is the distributed Notes Platform;
- the **agentic monitoring infrastructure**, which collects telemetry, detects anomalies, stores knowledge and agentic state, exposes diagnostic tools, executes the multi-agent workflow, and provides the operator interface.

The monitored application does not depend on the internal reasoning logic of the agents. It shares telemetry and controlled diagnostic access with the monitoring infrastructure.

## 2. Monitoring requirements

The system must:

- collect CPU, memory, network, and service-latency metrics from the monitored containers;
- collect application, system, and relevant runtime logs;
- store telemetry in OpenSearch using service-specific indices;
- preserve service identity and timestamps in telemetry records;
- propagate request identifiers across the main application path for log correlation;
- provide OpenSearch Dashboards for manual expert inspection.

The current index convention is:

```text
metrics-<service>-YYYY.MM.DD
logs-<service>-YYYY.MM.DD
```

## 3. Anomaly detection requirements

OpenSearch Anomaly Detection provides online anomaly detection for the current metric-based experimental signals.

The following rules are mandatory:

- every detector must be **SINGLE_ENTITY**;
- multi-entity detectors must not be introduced in this project;
- each CPU and RAM detector must read the metric index of one monitored service;
- each latency detector must represent one explicit monitored path;
- detector creation must wait for enough historical intervals;
- anomaly grade, confidence, score, timestamps, detector identity, and detector semantics must remain available to the diagnostic workflow.

The current detector set contains **16 SINGLE_ENTITY detectors**:

- 5 CPU detectors using `docker_container_cpu.usage_percent`;
- 5 RAM detectors using `docker_container_mem.usage_percent`;
- 3 `NETLAT` detectors using `network_transport_latency.response_time`;
- 3 `APPLAT` detectors using `application_service_latency.response_time`.

Semantic log anomaly detection is outside the current implemented scope. Logs are used as diagnostic evidence after an incident is created.

## 4. Autonomous anomaly intake requirements

A detected anomaly must be admitted into a durable workflow before autonomous diagnosis begins.

The current backend must:

- persist normalized anomaly observations in MongoDB;
- deduplicate observations by their OpenSearch result identity;
- preserve waiting anomalies across backend restarts;
- process anomalies in FIFO order;
- allow only one active autonomous anomaly workflow at a time;
- link admitted anomalies to the incident created or correlated from them;
- recover interrupted `PROCESSING` or `RECOVERY` entries back to a safe waiting state after restart.

Synthetic anomaly injection is available only as an explicit test capability and can be disabled through configuration.

## 5. Incident and task persistence requirements

MongoDB is the source of truth for agentic state. OpenSearch is not used as the incident database.

The backend must persist:

- incidents and their lifecycle state;
- concise anomaly metadata;
- diagnosis and remediation conclusions;
- structured agent activity events;
- durable investigation tasks;
- anomaly-inbox state;
- triage, BDI, review, and collaboration metadata.

Mutable task state must remain in the dedicated task collection rather than being duplicated as independent state inside the incident document.

Raw metric histories and complete log payloads must not be copied into MongoDB incident records. They remain in OpenSearch and are referenced through structured evidence summaries.

## 6. Multi-agent requirements

The production runtime must contain exactly five professional roles:

- Technical Lead;
- System Engineer;
- Network Engineer;
- Application Engineer;
- Software Developer.

The runtime configuration must reject an unexpected role set or ordering.

The agents must:

- run as SPADE agents;
- communicate through Prosody/XMPP;
- use explicit AgentSpeak BDI goals and intentions for durable coordination decisions;
- use bounded ReAct execution for specialist diagnostic work;
- support dynamic specialist selection instead of a fixed all-agent pipeline;
- preserve role-specific responsibilities and prompts;
- expose observable activity without exposing private chain-of-thought.

## 7. Technical Lead requirements

The Technical Lead is responsible for orchestration and critical review.

For each incident, it must be able to:

- take the incident in charge;
- perform triage only after the incident reaches `TAKEN_IN_CHARGE`;
- infer a probable technical domain;
- select an available primary investigator;
- create and dispatch a durable investigation task;
- receive specialist results through XMPP;
- review the final evidence through its AgentSpeak BDI policy;
- commit a terminal or escalation decision.

A transient review failure may be retried without forcing the specialist to repeat the already completed investigation.

## 8. Specialist requirements

A specialist must first deliberate a delegated task through AgentSpeak before starting ReAct execution.

The specialist ReAct loop must:

- remain bounded by a maximum number of diagnostic steps and tool timeouts;
- keep the detector-reported signal as the incident anchor;
- start from static project grounding when RAG is available;
- let the reasoning model choose the next evidence family;
- let the tool model choose a compatible concrete tool;
- validate tool schema, evidence family, and target component before execution;
- use observations to update the causal hypothesis;
- stop with a structured, evidence-backed result or an explicit unconfirmed outcome.

The runtime must not encode scenario-specific sequences such as a fixed `CPU -> metrics -> processes` workflow.

## 9. Model-routing requirements

The current design separates reasoning from tool selection:

- Gemma is used for Technical Lead reasoning and specialist diagnostic reasoning;
- Qwen is used for specialist tool selection and argument generation;
- Granite Embedding is used for vector embeddings.

All model calls must use the local Ollama endpoint. A shared inference gate limits concurrent LLM access so the local GPU/runtime is not overloaded by uncontrolled parallel calls.

## 10. Peer collaboration requirements

Specialist collaboration must be dynamic and evidence-driven.

When the primary specialist cannot sufficiently confirm a root cause, it may:

- decide whether another technical domain is needed;
- select one peer role;
- contact that peer directly through XMPP;
- receive peer evidence with a bounded timeout;
- merge the peer result with its own investigation;
- return the combined result to the Technical Lead.

The Technical Lead does not need to authorize this peer-help step. Nested recursive peer-help chains must be prevented, and an unavailable peer must not destroy the already collected solo evidence.

## 11. Diagnostic tool requirements

Agents must not receive unrestricted shell access. Diagnosis must use the MCP Server and its explicit read-only tools.

The current diagnostic surface supports:

- detector-aligned CPU and memory history;
- recent logs and full-text log search;
- process listing, process detail, threads, and process trees;
- current container runtime statistics;
- disk, inode, and writable-layer information;
- current socket state;
- service DNS resolution;
- bounded TCP connectivity checks;
- bounded HTTP endpoint checks;
- ICMP reachability;
- Qdrant knowledge retrieval.

Docker diagnostic targets are restricted to the five monitored containers. Service-level TCP/HTTP checks are additionally restricted to components that expose registered application endpoints.

## 12. Knowledge and RAG requirements

The knowledge base must contain stable technical information useful for diagnosis, such as architecture, dependencies, service responsibilities, observability semantics, runtime knowledge, network topology, application behaviour, and troubleshooting guidance.

It must not contain scenario ground truth, expected evaluation answers, detector labels, or private hidden reasoning.

RAG is supporting context. Live telemetry, logs, connectivity checks, and runtime observations remain the source of truth for an active incident.

## 13. Operator requirements

The operator dashboard must remain outside the autonomous control loop.

It must provide a human-readable view of:

- waiting anomalies and incident state;
- diagnosis, root cause, confidence, and evidence summaries;
- remediation, verification, and validation information;
- the five agents and their current activity;
- concise structured action traces and tools used;
- health of the backend, MongoDB, OpenSearch, Qdrant, MCP, XMPP, and Ollama;
- downloadable incident reports.

The public operator API must be read-only. Autonomous incident creation and updates remain internal to the backend.

## 14. Safety and quality requirements

The current prototype follows these non-functional constraints:

- **Local-first execution:** LLM and embedding inference runs through local Ollama.
- **Human control:** potentially disruptive remediation is recommended, not autonomously executed by the current MCP tool set.
- **Reproducibility:** infrastructure, scenarios, and tests are repeatable through Docker Compose, pytest, and Windows PowerShell.
- **Modularity:** monitored system, telemetry, MCP, knowledge, agent runtime, persistence, and dashboard remain separated components.
- **Explainability:** diagnostic conclusions must be linked to explicit evidence and operational actions.
- **Auditability:** incidents, tasks, anomaly intake, and agent activity are durably persisted.
- **Fault tolerance:** transient workflow failures and restarts must not silently lose admitted anomalies or completed specialist evidence.
- **Security by design:** no generic shell is exposed to the reasoning model, targets are allow-listed, and tool arguments are schema validated.
