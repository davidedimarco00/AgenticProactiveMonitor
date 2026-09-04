import pytest

from conftest import assert_success


pytestmark = pytest.mark.integration


EXPECTED_TOOLS = {
    "ping",
    "get_metrics",
    "get_logs",
    "search_logs",
    "get_processes",
    "get_runtime_stats",
    "get_disk_usage",
    "get_network_connections",
    "get_process_threads",
    "inspect_process",
    "get_process_tree",
    "resolve_service_dns",
    "test_icmp_reachability",
    "test_tcp_connection",
    "check_http_endpoint",
    "search_knowledge",
}

FORBIDDEN_GENERIC_TOOLS = {
    "run_shell",
    "shell",
    "run_command",
    "docker_exec",
    "ssh_exec",
}


def test_expected_tools_are_exposed(mcp_client):
    tool_names = mcp_client.list_tools()

    missing = EXPECTED_TOOLS - tool_names
    assert not missing, f"Missing MCP tools: {sorted(missing)}"


def test_generic_command_tools_are_not_exposed(mcp_client):
    tool_names = mcp_client.list_tools()

    exposed = FORBIDDEN_GENERIC_TOOLS & tool_names
    assert not exposed, f"Unsafe generic tools exposed: {sorted(exposed)}"


def test_ping(mcp_client):
    response = mcp_client.call_tool("ping")
    payload = assert_success(response)

    assert payload["service"] == "AgenticProactiveMonitor MCP"
