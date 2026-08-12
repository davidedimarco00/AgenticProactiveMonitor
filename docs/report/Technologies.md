# Technologies

This project uses a local-first technology stack. The components are selected to keep the monitoring pipeline, anomaly detection, knowledge retrieval, LLM inference, and agent communication independently replaceable.

## OpenSearch 3.6.0

OpenSearch is the central telemetry and incident data platform.

It is currently used for:

- metrics storage;
- log storage;
- OpenSearch Anomaly Detection;
- incident persistence;
- agent activity persistence;
- dashboard queries.

OpenSearch Dashboards 3.6.0 is used for manual inspection and data views.

## Telegraf

Telegraf runs inside every monitored container and collects infrastructure and network metrics.

The current configuration includes measurements for:

- container CPU;
- container memory;
- network interfaces;
- ping/RTT;
- service response time.

The data is sent directly to OpenSearch.

## Fluent Bit

Fluent Bit collects application and system logs from the monitored containers and sends them to OpenSearch.

Together with Telegraf, it provides the two main observability channels used during diagnosis:

```text
metrics + logs
```

## Qdrant 1.18.1

Qdrant is the vector database used for the technical knowledge base.

The default thesis collection is:

```text
thesis-knowledge-base
```

The current default vector configuration is:

```text
vector size: 384
distance: Cosine
```

Qdrant is used by the RAG pipeline and by the MCP `search_knowledge()` tool.

## Ollama

Ollama runs directly on the Windows host instead of inside Docker.

This makes local GPU inference available while the Docker infrastructure remains independent from the LLM runtime.

The infrastructure currently prepares the following models by default:

- `gemma4:e2b` for reasoning and text generation experiments;
- `qwen3.5:4b` for tool-oriented experiments;
- `qwen2.5:latest` as an additional local language model;
- `ibm/granite-embedding:30m` for embeddings and RAG.

Docker containers reach Ollama through:

```text
http://host.docker.internal:11434
```

## Model Context Protocol

The MCP Server is implemented in Python using `mcp[cli]>=2,<3`.

MCP provides a structured boundary between the reasoning system and diagnostic capabilities. Instead of allowing direct command execution, the project exposes explicit tools with defined inputs and controlled behaviour.

The MCP server also uses:

- `httpx` for HTTP communication;
- the Python Docker SDK for live container inspection.

## SPADE

SPADE is the selected Python framework for the multi-agent runtime.

It provides autonomous agent behaviours and XMPP-based inter-agent communication. The repository already includes working sender/receiver tests. The final specialist runtime will build on this communication layer.

## Prosody 13.0

Prosody is the XMPP server used by SPADE agents.

The current local domain is:

```text
xmpp
```

Prosody runs as part of the agentic infrastructure and exposes the XMPP client port on localhost.

## Flask

Flask is used for operator-facing web components.

Current Flask components include:

- the Notes Platform API Gateway and web interface;
- the agentic operator dashboard;
- the knowledge-base upload and ingestion interface.

## FastAPI

FastAPI is used by application services in the monitored Notes Platform.

The current FastAPI services are:

- `processing-service`;
- `data-service`.

FastAPI is also useful in this prototype because it provides explicit HTTP APIs and predictable service boundaries that can be monitored and diagnosed.

## SQLite

SQLite is used by the monitored Data Service for note persistence.

It keeps the monitored application self-contained while still providing a real persistence dependency that can produce downstream failures and error propagation during controlled experiments.

## Docker and Docker Compose

Docker is the execution environment for the monitoring infrastructure and the monitored workload.

Two independent Compose projects are used:

```text
agentic-proactive-monitor-infrastructure
monitored-system
```

The separation is intentional. The application under observation does not run inside the same Compose project as the agentic monitoring stack.

## VitePress

VitePress is used to build this documentation website.

The documentation is stored under:

```text
docs/
```

and is automatically deployed to GitHub Pages after documentation changes reach `main`.

The site also uses Mermaid support for architecture and workflow diagrams.

## GitHub Actions

GitHub Actions is used for repository automation.

The current workflows provide:

- Conventional Commit validation on pull requests;
- automatic GitHub Releases after pushes to `main`;
- automatic VitePress build and GitHub Pages deployment when documentation changes.

## PowerShell

Windows PowerShell is the main command-line environment used during development and experiments.

Fault injection, monitored-system tests, environment setup, and most operator commands are therefore exposed as PowerShell-compatible commands rather than Bash-only developer instructions.
