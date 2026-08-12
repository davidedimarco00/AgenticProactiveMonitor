# Requirements Analysis

This section describes the requirements of the current thesis prototype. Requirements are divided into monitoring, anomaly detection, diagnosis, agent coordination, operator interaction, and quality requirements.

## 1. System boundaries

AgenticProactiveMonitor is composed of two separated systems:

- the **monitored system**, which is the distributed Notes Platform;
- the **agentic monitoring infrastructure**, which collects telemetry, detects anomalies, stores knowledge, exposes diagnostic tools, and supports the agentic investigation.

The monitored application must not depend on the internal logic of the agentic system. The only shared boundary is observability and diagnostic access.

## 2. Monitoring requirements

The system must:

- collect CPU, memory, network, and service-related metrics from every monitored container;
- collect application and system logs from every monitored container;
- store telemetry in OpenSearch using service-specific indices;
- preserve service identity and timestamps in every telemetry record;
- propagate request identifiers across the main application path to support log correlation;
- expose OpenSearch Dashboards for manual inspection of metrics and logs.

## 3. Anomaly detection requirements

OpenSearch Anomaly Detection must provide online anomaly detection for the current experimental signals.

The detector configuration must satisfy the following rules:

- every detector must be **SINGLE_ENTITY**;
- each CPU detector must read only the metric index of one monitored service;
- each RAM detector must read only the metric index of one monitored service;
- each network-latency detector must represent one explicit source-to-destination application link;
- detectors must not be configured as multi-entity detectors;
- detector bootstrap must wait for enough historical metric intervals before enabling a detector;
- anomaly grade, confidence, anomaly score, detector state, and timestamps must remain available for later analysis.

The current scope requires 13 detectors: 5 CPU, 5 RAM, and 3 network-service-latency detectors.

## 4. Controlled evaluation requirements

The prototype must provide repeatable fault scenarios that can be started and stopped independently from Windows PowerShell.

The current scenarios must cover:

- CPU saturation;
- progressive memory pressure;
- real network delay and jitter;
- application processing delay;
- downstream data-service unavailability.

Each scenario must restore the monitored system to a normal state after execution. Detector-oriented experiments must include a clean recovery period before re-injecting the same type of fault.

The test suite must record detector misses as well as successful detections to avoid experimental selection bias.

## 5. Diagnostic tool requirements

Agents must not receive unrestricted shell access. Live diagnosis must use a controlled tool interface exposed by the MCP Server.

The current tool set must support:

- retrieval of CPU and memory metrics;
- retrieval of recent logs;
- full-text log search;
- inspection of container processes;
- inspection of container runtime statistics;
- inspection of filesystem and writable-layer usage;
- inspection of TCP/UDP sockets and active connections;
- semantic retrieval from the Qdrant knowledge base.

Docker live diagnostics must be restricted to the five monitored containers. Current diagnostic tools are read-only.

## 6. Knowledge and RAG requirements

The knowledge base must contain stable technical information useful for diagnosis, including:

- architecture and dependencies;
- service responsibilities and endpoints;
- observability semantics;
- Linux and container troubleshooting knowledge;
- network topology and network evidence;
- application behaviour and error propagation;
- known diagnostic patterns.

The knowledge base must not contain:

- scenario ground truth;
- expected test answers;
- detector evaluation labels;
- fault-injection parameters used only for experiments;
- private agent prompts or hidden reasoning.

Retrieved knowledge is supporting context. Live metrics, logs, and runtime evidence remain the source of truth for active incidents.

## 7. Multi-agent requirements

The target agentic runtime must model a virtual technical team with five specialist roles:

- Technical Lead;
- System Engineer;
- Network Engineer;
- Application Engineer;
- Software Developer.

Each specialist must have its own role-specific objective and diagnostic competence.

The final agent runtime must combine:

- explicit BDI state for beliefs, desires, and intentions;
- a ReAct loop for reasoning, action selection, tool invocation, observation, and further reasoning;
- dynamic delegation instead of a mandatory fixed specialist pipeline;
- inter-agent communication through SPADE/XMPP;
- local LLM inference through Ollama;
- MCP tool use for live diagnostics;
- Qdrant retrieval for technical context.

The Technical Lead must coordinate the investigation and critically review the evidence before a final diagnosis is presented.

## 8. Operator requirements

The operator dashboard must provide a human-readable view of the system without exposing private model chain-of-thought.

The interface must show:

- active and historical incidents;
- anomaly metadata;
- investigation status;
- diagnosis and supporting evidence;
- diagnosis confidence when available;
- proposed remediation and verification steps;
- health of the monitoring infrastructure;
- virtual team members and current activity;
- concise operational reasons for agent actions.

Agent events must describe observable actions and outcomes, not hidden reasoning traces.

## 9. Safety requirements

The prototype follows a human-in-the-loop policy.

Potentially disruptive remediation must not be executed automatically without an explicit control policy. The current MCP tools are diagnostic and read-only. Future write tools must be introduced through an allowlist and must include clear preconditions, expected effects, validation, and operator control where appropriate.

## 10. Quality requirements

The system should satisfy the following non-functional requirements:

- **Local-first execution:** LLM and embedding inference should be executable locally through Ollama.
- **Reproducibility:** infrastructure, scenarios, and tests should be repeatable from Docker Compose and PowerShell.
- **Modularity:** monitored workload, observability infrastructure, MCP tools, dashboard, and agent runtime should remain separated components.
- **Explainability:** diagnoses should be connected to explicit evidence and operational actions.
- **Auditability:** incidents and agent activity should be persistable and searchable.
- **Portability:** development commands must support Windows PowerShell and Docker Desktop.
- **Security by design:** secrets must not be committed and generic shell execution should not be exposed to the reasoning model.
