# MCP Server

This module provides the **Model Context Protocol (MCP) server** used by the Agentic Proactive Monitor project.

Its purpose is to expose controlled, read-only diagnostic tools that can later be invoked autonomously by SPADE agents and local LLMs during incident investigation.

## Architecture

```text
Diagnostic Agent / LLM
        |
        v
     MCP Server
        |
   +----+---------+
   |              |
OpenSearch      Docker
   |              |
Metrics/Logs   Live Host State
   |
   +------------ Qdrant / Ollama
                  |
             Knowledge Base
```

The server uses **Streamable HTTP** and is available locally at:

```text
http://127.0.0.1:8000/mcp
```

## Implemented Tools

### OpenSearch

- `get_metrics()`
  Retrieves CPU or memory metrics for a monitored machine.

- `get_logs()`
  Retrieves recent logs using time, level and source filters.

- `search_logs()`
  Performs full-text searches on application and system logs.

### Docker Live Diagnostics

- `get_processes()`
  Retrieves running processes ordered by CPU usage.

- `get_runtime_stats()`
  Retrieves container-specific CPU, memory, PID and uptime statistics.

- `get_disk_usage()`
  Retrieves filesystem pressure, inode usage and container writable-layer size.

- `get_network_connections()`
  Retrieves TCP/UDP sockets and active network connections.

### Knowledge Base / RAG

- `search_knowledge()`
  Embeds a query using Ollama (`ibm/granite-embedding:30m`) and retrieves the most relevant chunks from Qdrant collection `thesis-knowledge-base`.

## Security Design

The MCP server does **not** expose a generic shell or arbitrary Docker command.

Allowed monitored targets are restricted to:

```text
machine-01
machine-02
machine-03
machine-04
machine-05
```

Docker diagnostic commands are fixed inside the MCP implementation.

Current tools are read-only.

## Docker Integration

The MCP container accesses:

```text
OpenSearch -> http://opensearch:9200
Qdrant    -> http://qdrant:6333
Ollama    -> http://ollama:11434
```

Docker live diagnostics use:

```text
/var/run/docker.sock
```

## Current Status

The MCP infrastructure has been validated using MCP Inspector.

All current diagnostic and RAG tools are operational and ready to be integrated with the SPADE Diagnostic Agent and LLM reasoning layer.
