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
Metrics/Logs   Live State      monitored-system
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

### Controlled Diagnostic Execution

The diagnostic execution layer allows a specialist to collect additional live evidence instead of merely recommending that an operator run a diagnostic command later.

- `get_process_threads()` inspects threads of one process and their CPU/memory usage.
- `inspect_process()` reads selected `/proc/<pid>` status, command-line and I/O information.
- `get_process_tree()` returns parent/child process relationships with resource usage.
- `resolve_service_dns()` resolves one allow-listed monitored service from another monitored service.
- `test_tcp_connection()` performs one bounded TCP connect test between allow-listed monitored services.
- `check_http_endpoint()` performs one read-only HTTP GET between allow-listed monitored services.

These tools are intentionally **not** a generic shell. Their commands, protocols and target space are fixed or validated by the implementation. They are intended for autonomous diagnosis, not autonomous remediation.

### Knowledge Base / RAG

`search_knowledge(query, limit=5)` embeds the query using Ollama and retrieves relevant chunks from the single shared Qdrant collection:

```text
monitored-system
```

This collection contains documentation specific to the concrete monitored Notes Platform: architecture, services, dependencies, telemetry semantics and implemented application behaviour.

There are no role-specific knowledge collections. General Linux, networking, application and software knowledge is expected to come from the LLM's pretrained knowledge. Agent specialisation is implemented through role, responsibilities, reasoning and available tools rather than separate RAG corpora.

The knowledge tool is read-only. Retrieved documents provide system-specific context; live OpenSearch, Docker and other runtime observations remain the evidence used by the agents to formulate a diagnosis.

## Safety Boundary

Docker access is restricted to the five containers of the standalone monitored system:

```text
traffic-generator
api-gateway
processing-service
data-service
worker-service
```

The MCP server does not expose `run_shell`, `docker_exec`, `ssh_exec` or another arbitrary command primitive. Diagnostic actions are bounded and read-only. Actions that change service state, terminate processes, restart containers, change configuration or deploy code remain operator-facing remediation recommendations produced by the Technical Lead.

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
