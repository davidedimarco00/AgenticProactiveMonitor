import pytest

from conftest import assert_success


pytestmark = pytest.mark.integration


def test_get_processes(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_processes",
        {
            "host_id": test_host,
            "limit": 5,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["container_status"] == "running"
    assert 0 < payload["returned_processes"] <= 5

    first_process = payload["processes"][0]
    assert first_process["pid"] > 0
    assert first_process["command"]
    assert first_process["cpu_percent"] >= 0
    assert first_process["memory_percent"] >= 0


def test_get_runtime_stats(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_runtime_stats",
        {"host_id": test_host},
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["container_status"] == "running"
    assert payload["cpu"]["usage_percent"] >= 0
    assert payload["cpu"]["available_cpus"] >= 1
    assert payload["memory"]["used_bytes"] >= 0
    assert payload["memory"]["limit_bytes"] > 0
    assert 0 <= payload["memory"]["usage_percent"] <= 100
    assert payload["pids"] > 0
    assert payload["uptime_seconds"] > 0


def test_get_disk_usage(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_disk_usage",
        {"host_id": test_host},
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["container_status"] == "running"

    filesystem = payload["filesystem"]
    assert filesystem["mount_point"] == "/"
    assert filesystem["total_bytes"] > 0
    assert filesystem["used_bytes"] >= 0
    assert filesystem["available_bytes"] >= 0
    assert 0 <= filesystem["usage_percent"] <= 100

    inodes = payload["inodes"]
    assert inodes["total"] > 0
    assert inodes["used"] >= 0
    assert 0 <= inodes["usage_percent"] <= 100

    storage = payload["container_storage"]
    assert storage["writable_layer_bytes"] >= 0
    assert storage["rootfs_bytes"] > 0


def test_get_network_connections(mcp_client, test_host):
    response = mcp_client.call_tool(
        "get_network_connections",
        {
            "host_id": test_host,
            "limit": 30,
        },
    )
    payload = assert_success(response)

    assert payload["host_id"] == test_host
    assert payload["container_status"] == "running"
    assert 0 < payload["returned_connections"] <= 30
    assert len(payload["connections"]) == payload["returned_connections"]
    assert payload["state_counts"]

    first_connection = payload["connections"][0]
    assert first_connection["protocol"] in {"tcp", "udp"}
    assert first_connection["state"]
    assert first_connection["local_address"]
    assert first_connection["peer_address"]
