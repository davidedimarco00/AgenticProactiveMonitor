from tools.opensearch_tools import METRICS


def test_cpu_metric_matches_single_entity_detector_field() -> None:
    assert METRICS["cpu"]["measurement"] == "docker_container_cpu"
    assert METRICS["cpu"]["field"] == "docker_container_cpu.usage_percent"
    assert METRICS["cpu"]["scope"] == "container"


def test_memory_metric_matches_single_entity_detector_field() -> None:
    assert METRICS["memory"]["measurement"] == "docker_container_mem"
    assert METRICS["memory"]["field"] == "docker_container_mem.usage_percent"
    assert METRICS["memory"]["scope"] == "container"


def test_network_metric_matches_single_entity_netlat_detector_field() -> None:
    assert METRICS["network_transport_latency"]["measurement"] == "network_transport_latency"
    assert METRICS["network_transport_latency"]["field"] == "network_transport_latency.response_time"
    assert METRICS["network_transport_latency"]["scope"] == "service_path"
