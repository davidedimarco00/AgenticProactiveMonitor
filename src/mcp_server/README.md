# MCP Server

This module provides the **Model Context Protocol (MCP) server** used by AgenticProactiveMonitor.

Its purpose is to expose controlled diagnostic tools that can be invoked by SPADE agents and local LLMs during incident investigation.

## Architecture

```text
Specialist Agent / LLM
        |
        v
     MCP Server
        |
   +----+---------+------------------+
   |              |                  |
OpenSearch      Docker            Qdrant
   |              |                  |
Metrics/Logs   Live State     Knowledge Collections
                                      |
                                   Ollama
                                  Embeddings
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

`search_knowledge(query, limit=5, role=None, scope="auto")` embeds the query using Ollama and retrieves relevant chunks from Qdrant.

Knowledge is separated into:

```text
monitored-system
    shared documentation of the concrete monitored Notes Platform

kb-system-engineer-linux
kb-network-engineer
kb-application-engineer
kb-software-developer
kb-technical-lead
    professional/domain collections associated with specialist roles
```

The supported scopes are:

- `auto`: shared collection only when no role is supplied; shared + role collection when a role is supplied;
- `shared`: `monitored-system` only;
- `role`: only the professional collection for the supplied role;
- `both`: shared + professional collection.

`limit` is applied per searched collection. Results are returned grouped by collection and as a score-sorted merged list. This prevents higher-scoring shared chunks from hiding all role-specific context.

The knowledge tool is read-only. Retrieved documents provide technical context; live OpenSearch, Docker and other runtime observations remain the evidence used by the agents to formulate a diagnosis.

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

The MCP container accesses infrastructure services through Docker networking:

```text
OpenSearch -> http://opensearch:9200
Qdrant    -> http://qdrant:6333
```

Ollama runs natively on the Windows host and is reached from Docker through the configured `OLLAMA_URL`, normally:

```text
http://host.docker.internal:11434
```

Docker live diagnostics use:

```text
/var/run/docker.sock
```

Because the monitored containers have explicit names, the MCP server can inspect them even though they belong to the separate `monitored-system` Compose project.
