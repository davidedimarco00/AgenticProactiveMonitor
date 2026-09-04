import pytest

from conftest import assert_success


pytestmark = pytest.mark.integration


def test_tcp_connection_resolves_processing_service_internal_port(mcp_client):
    response = mcp_client.call_tool(
        "test_tcp_connection",
        {
            "host_id": "api-gateway",
            "target_host": "processing-service",
        },
    )
    payload = assert_success(response)

    assert payload["target_host"] == "processing-service"
    assert payload["port"] == 8000
    assert payload["connected"] is True
    assert payload["endpoint_source"] == "authoritative_monitored_system_topology"


def test_http_check_resolves_processing_service_internal_port(mcp_client):
    response = mcp_client.call_tool(
        "check_http_endpoint",
        {
            "host_id": "api-gateway",
            "target_host": "processing-service",
            "path": "/health",
        },
    )
    payload = assert_success(response)

    assert payload["target_host"] == "processing-service"
    assert payload["port"] == 8000
    assert payload["url"] == "http://processing-service:8000/health"
    assert payload["http_status"] == 200
    assert payload["endpoint_source"] == "authoritative_monitored_system_topology"
