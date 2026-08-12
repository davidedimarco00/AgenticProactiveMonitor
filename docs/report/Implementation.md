# Implementation

This page describes what is currently implemented in the repository and what is still under development.

## 1. Repository organisation

The current project is divided into four main runtime areas:

```text
src/
├── infrastructure/
├── monitored_system/
├── mcp_server/
└── agentic_dashboard/
```

The autonomous multi-agent backend is the next major component to be completed. The infrastructure already contains the services required by that backend: XMPP, Ollama access, Qdrant, OpenSearch, and MCP.

## 2. Agentic infrastructure

The infrastructure Compose project is named:

```text
agentic-proactive-monitor-infrastructure
```

It currently includes:

- OpenSearch;
- OpenSearch Dashboards;
- Qdrant;
- Qdrant collection bootstrap;
- a knowledge-base web interface;
- Open WebUI;
- Prosody/XMPP;
- the MCP Server;
- the operator dashboard;
- OpenSearch index bootstrap;
- OpenSearch Dashboards bootstrap;
- OpenSearch detector bootstrap.

The monitored application is intentionally excluded from this Compose project.

Ollama runs natively on Windows and is reached from Docker through:

```text
http://host.docker.internal:11434
```

This configuration allows the local models to use the NVIDIA GPU while Docker Desktop runs the Linux containers used by the monitoring system.

## 3. Monitored Notes Platform

The monitored application is implemented as a second Docker Compose project called `monitored-system`.

The main request path is:

```text
traffic-generator
      -> api-gateway
      -> processing-service
      -> data-service
      -> SQLite
```

The platform supports normal note operations such as create, read, update, and delete. The traffic generator performs real HTTP requests to produce a continuous baseline workload.

`worker-service` is independent from the request path and is used as a synthetic background service.

## 4. Telemetry implementation

Every monitored container includes Telegraf and Fluent Bit.

Telegraf collects infrastructure and service-level metrics, including:

- container CPU usage;
- container memory usage;
- network interface counters;
- ICMP ping information;
- network-service response time.

Fluent Bit collects application and system logs.

The output index convention is:

```text
metrics-<service>-YYYY.MM.DD
logs-<service>-YYYY.MM.DD
```

The monitored services also generate heartbeat logs and propagate `X-Request-ID` values through the application request chain.

## 5. OpenSearch bootstrap

Startup scripts automatically prepare the OpenSearch environment.

Implemented bootstrap tasks include:

- index templates for metrics and logs;
- OpenSearch Dashboards data views for each monitored service;
- CPU anomaly detectors;
- RAM anomaly detectors;
- network-service-latency anomaly detectors.

The detector bootstrap is parameterised and waits for historical intervals before creating and enabling the detector.

All 13 current detectors are **SINGLE_ENTITY**.

## 6. Fault injection scenarios

Controlled scenarios are implemented under:

```text
src/monitored_system/infrastructure/scenarios/
```

The available scenarios are:

- `cpu-spike`;
- `memory-leak`;
- `network-latency`;
- `high-latency`;
- `data-service-down`.

The network scenario uses Linux traffic control with `tc/netem`. It introduces packet delay and optional jitter on the application path from `api-gateway` to `processing-service`.

The `high-latency` scenario is intentionally different: it introduces delay inside the application. This separation is useful for later diagnosis because similar user-visible latency can have different root causes.

All scenario controls are available through Windows PowerShell scripts.

## 7. Test suite

A repeatable PowerShell test suite is implemented under:

```text
src/monitored_system/infrastructure/tests/
```

The suite validates:

- container availability;
- Notes Platform health;
- metrics and logs for every monitored service;
- detector state;
- the requirement that every detector is `SINGLE_ENTITY`;
- CPU anomaly detection;
- RAM anomaly detection;
- network-latency anomaly detection;
- application-latency behaviour;
- downstream service outage behaviour.

Detector experiments include a recovery window before fault injection. Results can be written to JSON and appended to a detector-confidence CSV history containing anomaly grade, confidence, score, detection latency, baseline values, peak values, and detector misses.

## 8. Knowledge base

The monitored-system knowledge base is located under:

```text
src/monitored_system/knowledge_base/
```

It is organised into:

```text
shared/
services/
domains/
diagnostics/
```

The content is written to support the five professional roles of the target multi-agent team. Metadata is included so retrieval can be filtered by role, service, domain, and incident type.

The knowledge base explicitly excludes test-suite results, controlled scenario ground truth, and expected evaluation answers.

## 9. Qdrant and knowledge ingestion

The infrastructure creates the Qdrant collection automatically. The default configuration uses:

```text
collection: thesis-knowledge-base
vector size: 384
distance: Cosine
embedding model: ibm/granite-embedding:30m
```

A Flask knowledge-base web service is also present in the infrastructure. It can process documents, generate embeddings through Ollama, and store chunks in Qdrant.

## 10. MCP Server

The MCP Server is implemented in Python and uses the Model Context Protocol Streamable HTTP transport.

Its endpoint is:

```text
http://127.0.0.1:8000/mcp
```

Implemented OpenSearch tools:

- `get_metrics()`;
- `get_logs()`;
- `search_logs()`.

Implemented Docker diagnostic tools:

- `get_processes()`;
- `get_runtime_stats()`;
- `get_disk_usage()`;
- `get_network_connections()`.

Implemented RAG tool:

- `search_knowledge()`.

Docker inspection is restricted to:

```text
traffic-generator
api-gateway
processing-service
data-service
worker-service
```

There is no generic shell tool. The current diagnostic surface is read-only.

The MCP module includes pytest tests for protocol behaviour, validation, OpenSearch tools, Docker tools, and Qdrant retrieval.

## 11. XMPP and SPADE communication

Prosody is configured as the XMPP server with the domain:

```text
xmpp
```

The repository includes SPADE sender and receiver examples. The communication path has been validated by sending messages between SPADE agents through the Prosody server.

This proves the communication infrastructure required by the final multi-agent backend, but it is not yet the complete specialist reasoning runtime.

## 12. Operator dashboard

The Flask operator dashboard is implemented under:

```text
src/agentic_dashboard/
```

The default endpoint is:

```text
http://127.0.0.1:5050
```

The dashboard supports:

- incident creation, listing, update, and detail views;
- system health checks;
- incident persistence in OpenSearch;
- agent-event persistence in OpenSearch;
- representation of the five-role virtual operations team;
- per-role activity inspection;
- diagnosis, evidence, remediation, risk, and verification information.

Current incident indices use:

```text
agentic-incidents-YYYY.MM
agentic-agent-events-YYYY.MM
```

The current dashboard contains a temporary compatibility mapping for older agent identities. This allows the user interface to represent the target five-role team before the final agent runtime is fully refactored.

## 13. Agentic backend status

The final autonomous backend is **not yet fully implemented** in the current repository state.

The target implementation will connect the already validated components:

```text
SPADE/XMPP
   + BDI state
   + ReAct loop
   + Ollama local models
   + MCP diagnostic tools
   + Qdrant RAG
   + OpenSearch anomaly events
   + operator dashboard
```

The important next implementation task is therefore the real specialist control loop, not the monitoring infrastructure itself.
