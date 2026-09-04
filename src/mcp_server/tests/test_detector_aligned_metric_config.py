from tools.opensearch_tools import METRICS


def test_cpu_metric_matches_single_entity_detector_field() -> None:
    assert METRICS["cpu"]["measurement"] == "docker_container_cpu"
    assert METRICS["cpu"]["field"] == "docker_container_cpu.usage_percent"
    assert METRICS["cpu"]["scope"] == "container"


def test_memory_metric_matches_single_entity_detector_field() -> None:
    assert METRICS["memory"]["measurement"] == "docker_container_mem"
    assert METRICS["memory"]["field"] == "docker_container_mem.usage_percent"
    assert METRICS["memory"]["scope"] == "container"
