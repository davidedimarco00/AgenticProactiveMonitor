# MCP Integration Test Suite

These tests validate the running MCP server through the official MCP Python client and Streamable HTTP transport.

## Preconditions

- MCP server available at `http://127.0.0.1:8000/mcp`.
- `machine-03` running and producing metrics/logs.
- OpenSearch, Qdrant and Ollama running.
- The Qdrant knowledge base contains at least one document.

## Covered behaviour

- MCP connection, initialization and tool discovery.
- `ping`.
- OpenSearch metrics, logs and full-text log search.
- Docker processes, runtime statistics, disk usage and network connections.
- Qdrant/Ollama semantic knowledge search.
- Input validation and protection against invalid/infrastructure container targets.
- Verification that generic command/shell tools are not exposed.

## Run on Windows PowerShell

From `src\mcp_server`:

```powershell
python -m pip install -r requirements-test.txt
python -m pytest -v
```

Optional configuration:

```powershell
$env:MCP_TEST_URL="http://127.0.0.1:8000/mcp"
$env:MCP_TEST_HOST="machine-03"
$env:MCP_TEST_KB_QUERY="high CPU troubleshooting Linux"
python -m pytest -v
```

Run only one area:

```powershell
python -m pytest tests\test_docker_tools.py -v
python -m pytest tests\test_opensearch_tools.py -v
python -m pytest tests\test_qdrant_tools.py -v
python -m pytest tests\test_validation.py -v
```

The suite is intentionally integration-oriented: failures indicate that the MCP protocol, a tool implementation, or one of its real dependencies is not behaving as expected.
