# Technologies

AgenticProactiveMonitor uses a local-first technology stack. The architecture keeps observability, anomaly detection, persistence, knowledge retrieval, LLM inference, diagnostic tools, agent communication, and the operator interface separated so individual components can evolve independently.

## OpenSearch 3.6.0

OpenSearch is the observability and anomaly-detection platform.

It is used for:

- metrics storage;
- log storage;
- OpenSearch Anomaly Detection;
- detector results and manual observability queries.

OpenSearch is **not** the current incident database. Agentic incidents, tasks, and structured workflow state are stored in MongoDB.

OpenSearch Dashboards 3.6.0 provides manual inspection of service metrics, logs, and detector data.

## Telegraf

Telegraf runs inside every monitored container and collects infrastructure and service-level metrics.

The detector-related measurements include:

- container CPU usage;
- container memory usage;
- network transport latency;
- application-service latency.

Additional network and system measurements are also available for observability.

## Fluent Bit

Fluent Bit collects application, system, and relevant runtime logs from the monitored containers and forwards them to OpenSearch.

Metrics and logs are the two main live evidence channels used during agentic diagnosis.

## MongoDB 7.0

MongoDB is the durable persistence layer for the agentic workflow.

It stores:

- anomaly-inbox records;
- incidents;
- structured incident events;
- durable investigation tasks;
- triage, BDI, review, collaboration, diagnosis, and remediation metadata.

This separation keeps high-volume observability data in OpenSearch while MongoDB stores the workflow state required for recovery, operator views, and auditability.

## Qdrant 1.18.1

Qdrant is the vector database used for the technical knowledge base.

The current default collection is:

```text
monitored-system
```

with:

```text
vector size: 384
distance: Cosine
```

Qdrant is used by the RAG pipeline and the MCP `search_knowledge()` tool.

## Ollama

Ollama runs directly on the Windows host rather than inside Docker. This allows local GPU inference while the Docker infrastructure remains independent from the LLM runtime.

The current production model roles are:

- `gemma4:e4b` as the default Compose reasoning model;
- `qwen3.5:4b` for specialist tool selection and argument generation;
- `ibm/granite-embedding:30m` for embeddings and RAG.

Docker containers reach Ollama through:

```text
http://host.docker.internal:11434
```

The backend configuration keeps reasoning and tool-model names replaceable through environment variables, which supports later model-comparison experiments without changing the agent architecture.

## SPADE 4.1.4

SPADE is the runtime framework for the five autonomous agents.

It provides agent lifecycle management, behaviours, message templates, and XMPP communication. The current backend uses one Technical Lead and four specialists as real SPADE agents.

## SPADE-LLM 0.3.0

SPADE-LLM integrates LLM providers, interaction context, and MCP tool access into the SPADE agent environment.

The backend uses it to:

- connect local Ollama models to agents;
- discover and invoke MCP tools;
- provide the LLM/tool integration used by the specialist ReAct executor.

## AgentSpeak / py-agentspeak 0.3.0

The project uses `py-agentspeak` to execute explicit BDI policies.

The production source contains AgentSpeak plans for:

- Technical Lead triage, primary-investigator selection, and review;
- specialist task acceptance, investigation commitment, and peer help.

This makes BDI an executable part of the runtime rather than only an architectural description.

## LangChain and LangChain Ollama

The agentic backend uses LangChain 1.3.x and `langchain-ollama` 1.1.x in the LLM/tool execution layer.

The project-level ReAct executor adds its own structured evidence contracts, detector focus, validation, and bounded diagnostic policies on top of the generic model/tool primitives.

## Model Context Protocol

The MCP Server is implemented in Python using the MCP package and Streamable HTTP transport.

MCP provides a controlled boundary between reasoning and diagnostic capabilities. Instead of giving an LLM unrestricted command execution, the project exposes explicit tools with typed inputs, allow-listed targets, bounded parameters, and read-only behaviour.

The server uses:

- `httpx` for OpenSearch and service communication;
- the Python Docker SDK for container inspection;
- Pydantic-compatible parameter schemas for bounded arguments.

## Prosody 13.0

Prosody is the XMPP server used by the SPADE agents.

The current local domain is:

```text
xmpp
```

The five production identities are based on the roles `technical-lead`, `system-engineer`, `network-engineer`, `application-engineer`, and `software-developer`.

## FastAPI and Uvicorn

FastAPI is used by the agentic backend for its read-only operator API and by the Processing and Data Services in the monitored Notes Platform.

The agentic API exposes system status, incidents, timelines, reports, agent information, and structured agent events without exposing operator write endpoints for incident control.

Uvicorn serves the FastAPI application.

## Flask

Flask is used for operator-facing web interfaces:

- the Notes Platform API Gateway and web UI;
- the agentic operator dashboard;
- the knowledge-base web interface.

The dashboard consumes the backend FastAPI contract and does not persist incident state directly.

## SQLite

SQLite is used by the monitored Data Service for note persistence.

It keeps the monitored workload self-contained while still providing a real persistence dependency that can fail and propagate application errors during experiments.

## ReportLab

ReportLab is used by the backend to generate incident PDF reports containing structured incident, diagnosis, remediation, validation, and agent-activity information.

## pytest and pytest-bdd

The agentic backend uses pytest for unit and integration tests and `pytest-bdd` for Gherkin end-to-end acceptance scenarios.

The suite is separated into unit, integration, and e2e levels so isolated logic can be tested independently from the Dockerized infrastructure and live local models.

## Docker and Docker Compose

Docker is the execution environment for the monitoring infrastructure and monitored workload.

Two independent Compose projects are used:

```text
agentic-proactive-monitor-infrastructure
monitored-system
```

They share the `agentic-monitoring-net` observability network, while the monitored workload retains its own internal application network.

## VitePress and Mermaid

VitePress builds this documentation website from the `docs/` directory. Mermaid is used for architecture, workflow, and sequence diagrams.

Documentation changes on `main` are automatically built and deployed to GitHub Pages.

## GitHub Actions

GitHub Actions provides repository automation for:

- Conventional Commit validation;
- agentic backend unit tests;
- automatic patch releases after pushes to `main`;
- VitePress build and GitHub Pages deployment for documentation changes.

## PowerShell

Windows PowerShell is the main local command-line environment used for development and experiments.

Fault injection, monitored-system tests, environment preparation, and local validation instructions are therefore provided with Windows-compatible commands.
