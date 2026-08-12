import pytest


pytestmark = pytest.mark.integration


def test_hallucinated_host_id_is_rejected(mcp_client):
    response = mcp_client.call_tool(
        "get_runtime_stats",
        {"host_id": "machine-0:03"},
    )

    assert response.is_error is True


def test_infrastructure_container_cannot_be_targeted(mcp_client):
    response = mcp_client.call_tool(
        "get_runtime_stats",
        {"host_id": "agentic-opensearch"},
    )

    assert response.is_error is True


def test_invalid_metric_is_rejected(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_metrics",
        {
            "host_id": test_host,
            "metric": "disk",
            "minutes": 15,
        },
    )

    assert response.is_error is True


def test_invalid_process_limit_is_rejected(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_processes",
        {
            "host_id": test_host,
            "limit": 0,
        },
    )

    assert response.is_error is True


def test_empty_knowledge_query_is_rejected(mcp_client):
    response = mcp_client.call_tool(
        "search_knowledge",
        {
            "query": "   ",
            "limit": 5,
        },
    )

    assert response.is_error is True
