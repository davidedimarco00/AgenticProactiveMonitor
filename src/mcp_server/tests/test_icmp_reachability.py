import pytest

from conftest import assert_success
from tools.icmp_tools import _parse_ping_output


pytestmark = pytest.mark.integration


def test_parse_ping_output_keeps_negative_reachability_as_observation():
    payload = _parse_ping_output(
        "PING processing-service (172.20.0.4) 56(84) bytes of data.\n\n"
        "--- processing-service ping statistics ---\n"
        "3 packets transmitted, 0 received, 100% packet loss, time 2046ms\n"
    )

    assert payload is not None
    assert payload["reachable"] is False
    assert payload["packets_sent"] == 3
    assert payload["packets_received"] == 0
    assert payload["packet_loss_percent"] == 100.0
    assert payload["resolved_ip"] == "172.20.0.4"


def test_icmp_reachability_between_monitored_services(mcp_client):
    response = mcp_client.call_tool(
        "test_icmp_reachability",
        {
            "host_id": "api-gateway",
            "target_host": "processing-service",
            "count": 3,
            "timeout_seconds": 1,
        },
    )
    payload = assert_success(response)

    assert payload["observation_type"] == "icmp_reachability"
    assert payload["host_id"] == "api-gateway"
    assert payload["target_host"] == "processing-service"
    assert payload["packets_sent"] == 3
    assert payload["packets_received"] >= 1
    assert payload["packet_loss_percent"] < 100.0
    assert payload["reachable"] is True
    assert payload["rtt_avg_ms"] >= 0.0
