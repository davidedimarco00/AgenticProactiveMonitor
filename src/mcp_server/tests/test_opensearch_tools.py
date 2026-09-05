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
    assert payload["detector_aligned"] is True
    assert payload["samples"] > 0
    assert payload["summary"]["latest"] is not None
    assert payload["timeline"]


def test_get_metrics_returns_detector_aligned_transport_latency(mcp_client):
    response = mcp_client.call_tool(
        "get_metrics",
        {
            "host_id": "api-gateway",
            "metric": "network_transport_latency",
            "target_host": "processing-service",
            "minutes": 30,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == "api-gateway"
    assert payload["target_host"] == "processing-service"
    assert payload["observed_network_target"] == "processing-service"
    assert payload["metric"] == "network_transport_latency"
    assert payload["measurement_name"] == "network_transport_latency"
    assert payload["field"] == "network_transport_latency.response_time"
    assert payload["unit"] == "seconds"
    assert payload["scope"] == "service_path"
    assert payload["detector_aligned"] is True
    assert payload["samples"] > 0
    assert payload["summary"]["latest"] is not None
    assert payload["summary"]["latest_ms"] is not None
    assert payload["timeline"]
    assert "value_ms" in payload["timeline"][0]


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


def test_search_logs_returns_known_log(mcp_client, test_host):
    logs_response = mcp_client.call_tool(
        "get_logs",
        {
            "host_id": test_host,
            "minutes": 30,
            "level": "ALL",
            "source": "all",
            "limit": 10,
        },
    )
    logs_payload = assert_success(logs_response)

    known_messages = [
        log.get("message", "").strip()
        for log in logs_payload["logs"]
        if log.get("message") and log.get("message").strip()
    ]
    assert known_messages, "No searchable log message was returned by get_logs"

    query_text = known_messages[0]

    response = mcp_client.call_tool(
        "search_logs",
        {
            "host_id": test_host,
            "query_text": query_text,
            "minutes": 30,
            "limit": 10,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["query"] == query_text
    assert payload["total_matches"] > 0
    assert 0 < payload["returned_logs"] <= 10

    first_log = payload["logs"][0]
    assert first_log["score"] >= 0
    assert first_log["message"]
