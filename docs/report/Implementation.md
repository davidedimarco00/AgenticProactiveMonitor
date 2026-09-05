# Implementation

This page describes the current implementation available on the production backend branch and `main`.

## 1. Repository organisation

The current source tree is divided into five main runtime areas:

```text
src/
├── agentic_dashboard/
├── agentic_system/
├── infrastructure/
├── mcp_server/
└── monitored_system/
```

The autonomous multi-agent backend is now implemented under `src/agentic_system/`; it is no longer a planned component.

## 2. Agentic infrastructure

The infrastructure Compose project is named:

```text
agentic-proactive-monitor-infrastructure
```

It includes:

- OpenSearch and OpenSearch Dashboards;
- Qdrant and collection bootstrap;
- knowledge-base ingestion and web services;
- Open WebUI;
- MongoDB;
- Prosody/XMPP bootstrap and server;
- MCP Server;
- agentic backend;
- operator dashboard;
- OpenSearch index and detector bootstrap services.

The monitored application is intentionally excluded from this Compose project.

Ollama runs natively on Windows and is reached from Docker through:

```text
http://host.docker.internal:11434
```

## 3. Monitored Notes Platform

The monitored application is a second Docker Compose project named `monitored-system`.

The main request path is:

```text
traffic-generator
      -> api-gateway
      -> processing-service
      -> data-service
      -> SQLite
```

The platform supports note creation, reading, update, and deletion. The traffic generator performs real HTTP operations every few seconds so the monitoring system receives a continuous baseline workload.

`worker-service` is independent from the main request path and is used for controlled background-resource experiments.

## 4. Telemetry implementation

Every monitored container runs Telegraf and Fluent Bit.

Telegraf collects the metric families used for infrastructure and latency observation, including:

- container CPU usage;
- container memory usage;
- network interface information;
- ping/RTT information;
- network transport latency;
- application-service latency.

Fluent Bit forwards application, system, and relevant runtime logs.

The index convention is:

```text
metrics-<service>-YYYY.MM.DD
logs-<service>-YYYY.MM.DD
```

Application requests carry `X-Request-ID` values along the main service chain to support distributed log correlation.

## 5. OpenSearch detector bootstrap

Detector creation is implemented in:

```text
src/infrastructure/opensearch/init/create-anomaly-detectors.sh
```

The bootstrap waits for historical 1-minute intervals, validates the detector definition, creates or updates the detector, and starts it.

Current defaults include:

```text
detection interval: 1 minute
window delay:       1 minute
shingle size:       4
required history:   40 complete intervals
```

The current detector inventory contains **16 detectors, all SINGLE_ENTITY**:

- `CPU-<service>` for all five monitored services;
- `RAM-<service>` for all five monitored services;
- 3 `NETLAT-<source>-<destination>` detectors;
- 3 `APPLAT-<source>-<destination>` detectors.

The metric fields are detector-aligned:

```text
CPU     docker_container_cpu.usage_percent
RAM     docker_container_mem.usage_percent
NETLAT  network_transport_latency.response_time
APPLAT  application_service_latency.response_time
```

## 6. Fault-injection scenarios

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

The network scenario uses Linux `tc/netem` and can introduce delay and jitter. The application `high-latency` scenario instead adds delay inside the application. Keeping these causes separate is useful because the two scenarios can produce similar user-visible latency while requiring different diagnoses.

Scenario controls are exposed as Windows PowerShell scripts.

## 7. Monitored-system tests

The repeatable detector and scenario test suite is located under:

```text
src/monitored_system/infrastructure/tests/
```

It validates service availability, telemetry, detector configuration, fault execution, anomaly behaviour, and recovery. The test logic also enforces the project rule that every detector is `SINGLE_ENTITY`.

Detector experiments use clean recovery periods and can record both successful detections and misses.

## 8. Knowledge base and ingestion

The monitored-system knowledge base is located under:

```text
src/monitored_system/knowledge_base/
```

Current content is organised into shared architecture documents, service documents, and domain-oriented operational knowledge. It describes stable project facts without including fault-injection ground truth or expected evaluation answers.

The infrastructure ingests this content into the Qdrant collection:

```text
monitored-system
```

The default vector configuration is:

```text
vector size: 384
distance: Cosine
embedding model: ibm/granite-embedding:30m
```

## 9. MCP Server

The MCP Server is implemented under:

```text
src/mcp_server/
```

It uses Streamable HTTP and is exposed locally at:

```text
http://127.0.0.1:8000/mcp
```

The server registers five tool groups: OpenSearch, Docker, extended diagnostics, ICMP, and Qdrant.

### OpenSearch tools

- `get_metrics()` — detector-aligned CPU or memory history;
- `get_logs()` — recent logs with source and severity filtering;
- `search_logs()` — full-text log search.

### Docker/runtime tools

- `get_processes()`;
- `get_runtime_stats()`;
- `get_disk_usage()`;
- `get_network_connections()`.

### Extended diagnostic tools

- `get_process_threads()`;
- `inspect_process()`;
- `get_process_tree()`;
- `resolve_service_dns()`;
- `test_tcp_connection()`;
- `check_http_endpoint()`;
- `test_icmp_reachability()`.

### RAG tool

- `search_knowledge()`.

Docker access is restricted to the five monitored containers. Service-level TCP and HTTP checks use authoritative internal ports defined by the MCP layer. There is no generic shell tool and the diagnostic surface is read-only.

## 10. Agentic backend package

The production backend is implemented under:

```text
src/agentic_system/agentic_system/
├── agents/
├── api/
├── incidents/
├── integrations/
├── reasoning/
├── main.py
├── runtime.py
└── settings.py
```

The runtime configuration requires exactly five roles in this order:

```text
technical_lead
system_engineer
network_engineer
application_engineer
software_developer
```

Each agent has its own XMPP identity and health port.

## 11. Agent factory and model routing

`agents/factory.py` creates one Technical Lead and four specialists.

The current model split is:

```text
Technical Lead reasoning        -> Gemma
Specialist diagnostic reasoning -> Gemma
Specialist tool selection       -> Qwen
Embeddings                      -> Granite Embedding
```

A shared inference gate limits concurrent model calls. Specialists receive the same MCP endpoint and configure a bounded ReAct executor with a maximum of six diagnostic steps in the production factory.

## 12. AgentSpeak BDI implementation

The BDI runtime is implemented in:

```text
src/agentic_system/agentic_system/reasoning/bdi.py
```

and loads real AgentSpeak policies from:

```text
reasoning/plans/technical_lead.asl
reasoning/plans/specialist.asl
```

The Technical Lead policy handles incident triage, primary-investigator selection, specialist-result review, and final review commitment.

The specialist policy handles delegated-task acceptance, investigation commitment, and direct peer-help intentions.

Python hosts the AgentSpeak interpreter and provides bounded bridge actions. The BDI goals and beliefs remain explicit AgentSpeak constructs.

## 13. Specialist ReAct implementation

The canonical project-level executor is:

```text
reasoning/specialist_react.py
```

The production path is:

```text
AgentSpeak investigation intention
    -> detector-focused incident anchor
    -> initial RAG grounding
    -> Gemma evidence decision
    -> structured EvidenceRequest
    -> Qwen compatible tool selection
    -> Python schema/semantic/target validation
    -> MCP observation
    -> further reasoning or bounded finalization
```

Evidence families are semantic capabilities rather than scenario-specific workflows. The runtime therefore does not hard-code a mandatory sequence of tools for a CPU, memory, network, or application anomaly.

The monitored component vocabulary is derived from MCP tool schemas, reducing duplicated topology knowledge inside the reasoning layer.

## 14. Specialist peer collaboration

After completing its initial ReAct investigation, a specialist can decide that another domain is required.

If peer assistance is requested:

1. the primary specialist selects one peer role;
2. it sends a direct XMPP request;
3. the peer commits a dedicated AgentSpeak peer-help intention;
4. the peer runs a bounded investigation;
5. the primary specialist combines the returned evidence with its own result;
6. the combined result is sent to the Technical Lead for review.

The Technical Lead is not involved in authorizing this peer-help exchange. Timeouts and peer failures preserve the primary specialist's solo result.

## 15. Durable anomaly inbox and incident workflow

The backend persists normalized anomaly observations in MongoDB before they enter autonomous processing.

The inbox states include:

```text
WAITING
RECOVERY
PROCESSING
COMPLETED
DISMISSED
```

The runtime advertises `FIFO_SINGLE_ACTIVE`, with one active anomaly workflow at a time. Interrupted `PROCESSING` or `RECOVERY` observations can be returned to `WAITING` during backend recovery.

Incident lifecycle data includes states such as:

```text
NEW
TAKEN_IN_CHARGE
TRIAGED
UNDER_ANALYSIS
DIAGNOSED
OPERATOR_ACTION_REQUIRED
```

The workflow also maintains durable investigation tasks with attempts, retry information, idempotency keys, and outcomes.

## 16. FastAPI operator API

The backend exposes a read-only operator API on port `8082`.

The public contract includes endpoints for:

- health and readiness;
- system status and overview;
- incident list and detail;
- incident timelines;
- incident PDF reports;
- agents and agent events.

Incident creation and updates are internal backend operations and are not exposed as public HTTP write endpoints.

## 17. Operator dashboard

The Flask dashboard is implemented under:

```text
src/agentic_dashboard/
```

and is exposed by default at:

```text
http://127.0.0.1:5050
```

It consumes the FastAPI backend instead of persisting incidents itself. MongoDB is the incident/history store, while OpenSearch remains the telemetry and anomaly-detection store.

The interface includes anomaly and incident pages, live workflow information, per-agent activity, structured traces, tools used, loaded Ollama models, system health, and downloadable PDF reports.

## 18. Backend tests

The agentic backend test suite is divided into:

```text
unit/        isolated project logic
integration/ real infrastructure interactions
e2e/         Gherkin acceptance scenarios with pytest-bdd
```

Integration tests cover components such as XMPP communication, MCP, MongoDB, the incident API, runtime services, and live model routing. The live Gemma -> Qwen -> MCP inference test is opt-in because it requires the local Ollama models.

A GitHub Actions workflow also runs the backend unit suite with Python 3.12 for relevant pull requests and backend branch changes.
