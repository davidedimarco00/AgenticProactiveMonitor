# MCP Server

This module provides the **Model Context Protocol (MCP) server** used by AgenticProactiveMonitor.

Its purpose is to expose controlled diagnostic tools that can be invoked by SPADE agents and local LLMs during incident investigation.

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
Metrics/Logs   Live Service State
   |
   +------------ Qdrant / Ollama
                  |
             Knowledge Base
```

The MCP server belongs to the **agentic infrastructure**. The containers inspected through Docker belong to the separate **monitored-system** Compose project.

The server uses Streamable HTTP and is available locally at:

```text
http://127.0.0.1:8000/mcp
```

## Implemented Tools

### OpenSearch

- `get_metrics()` retrieves CPU or memory metrics for a monitored service.
- `get_logs()` retrieves recent logs using time, level and source filters.
- `search_logs()` performs full-text searches on application and system logs.

### Docker Live Diagnostics

- `get_processes()` retrieves running processes ordered by CPU usage.
- `get_runtime_stats()` retrieves container CPU, memory, PID and uptime statistics.
- `get_disk_usage()` retrieves filesystem, inode and writable-layer usage.
- `get_network_connections()` retrieves TCP/UDP sockets and active connections.

### Knowledge Base / RAG

- `search_knowledge()` embeds a query using Ollama and retrieves relevant chunks from the Qdrant knowledge base.

## Allowed Monitored Targets

Docker access is restricted to the five containers of the standalone monitored system:

```text
traffic-generator
api-gateway
processing-service
data-service
worker-service
```

The MCP server does not expose a generic shell. Diagnostic commands are fixed in the implementation and the current tools are read-only.

## Docker Integration

The MCP container accesses the agentic infrastructure through internal service names:

```text
OpenSearch -> http://opensearch:9200
Qdrant    -> http://qdrant:6333
Ollama    -> http://ollama:11434
```

Docker live diagnostics use:

```text
/var/run/docker.sock
```

Because the monitored containers have explicit names, the MCP server can inspect them even though they belong to the separate `monitored-system` Compose project.
