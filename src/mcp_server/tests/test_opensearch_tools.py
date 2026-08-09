import pytest

from conftest import assert_success


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("metric", ["cpu", "memory"])
def test_get_metrics_returns_recent_samples(mcp_client, test_host, metric):
    response = mcp_client.call_tool(
        "get_metrics",
        {
            "host_id": test_host,
            "metric": metric,
            "minutes": 30,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["metric"] == metric
    assert payload["unit"] == "percent"
    assert payload["samples"] > 0
    assert payload["summary"]["latest"] is not None
    assert payload["timeline"]


def test_get_logs_returns_recent_logs(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_logs",
        {
            "host_id": test_host,
            "minutes": 30,
            "level": "ALL",
            "source": "all",
            "limit": 10,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["total_matches"] > 0
    assert 0 < payload["returned_logs"] <= 10
    assert len(payload["logs"]) == payload["returned_logs"]

    first_log = payload["logs"][0]
    assert first_log["timestamp"]
    assert first_log["level"]
    assert first_log["message"]


def test_search_logs_returns_relevant_structure(mcp_client, test_host):
    response = mcp_client.call_tool(
        "search_logs",
        {
            "host_id": test_host,
            "query_text": "synthetic application",
            "minutes": 30,
            "limit": 10,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["query"] == "synthetic application"
    assert payload["total_matches"] > 0
    assert 0 < payload["returned_logs"] <= 10

    first_log = payload["logs"][0]
    assert first_log["score"] >= 0
    assert first_log["message"]
