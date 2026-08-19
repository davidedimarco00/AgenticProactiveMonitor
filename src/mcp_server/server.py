import os

from mcp.server import MCPServer

from tools.opensearch_tools import register_opensearch_tools
from tools.docker_tools import register_docker_tools
from tools.qdrant_tools import register_qdrant_tools


mcp = MCPServer(
    "AgenticProactiveMonitor MCP",
    instructions=(
        "Read-only diagnostic tools for the "
        "AgenticProactiveMonitor thesis project."
    ),
)


@mcp.tool()
def ping() -> dict:
    """
    Check that the AgenticProactiveMonitor MCP server is available.
    """

    return {
        "status": "ok",
        "service": "AgenticProactiveMonitor MCP",
    }


register_opensearch_tools(mcp)
register_docker_tools(mcp)
register_qdrant_tools(mcp)

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    path = os.getenv("MCP_PATH", "/mcp")

    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
    )