from pydantic import TypeAdapter

from tools.diagnostic_tools import (
    DiagnosticTimeout,
    HttpPath,
    SERVICE_INTERNAL_ENDPOINTS,
    ServiceTargetHostId,
    service_internal_port,
)
from tools.docker_tools import ConnectionLimit, ProcessLimit
from tools.icmp_tools import PingCount, PingTimeoutSeconds
from tools.opensearch_tools import (
    MetricName,
    ResultLimit,
    TimeWindowMinutes,
    TransportTargetHostId,
)
from tools.qdrant_tools import KnowledgeLimit, KnowledgeQuery


def _schema(annotation):
    return TypeAdapter(annotation).json_schema()


def test_docker_tool_limits_publish_numeric_bounds() -> None:
    process = _schema(ProcessLimit)
    connections = _schema(ConnectionLimit)

    assert process["minimum"] == 1
    assert process["maximum"] == 50
    assert connections["minimum"] == 1
    assert connections["maximum"] == 100


def test_opensearch_tool_limits_publish_numeric_bounds() -> None:
    minutes = _schema(TimeWindowMinutes)
    limit = _schema(ResultLimit)

    assert minutes["minimum"] == 1
    assert minutes["maximum"] == 120
    assert limit["minimum"] == 1
    assert limit["maximum"] == 100


def test_opensearch_metric_schema_exposes_detector_aligned_netlat_input() -> None:
    metric = _schema(MetricName)
    target = _schema(TransportTargetHostId)

    assert set(metric["enum"]) == {
        "cpu",
        "memory",
        "network_transport_latency",
    }
    assert set(target["enum"]) == {
        "api-gateway",
        "processing-service",
        "data-service",
    }


def test_qdrant_tool_schema_publishes_query_and_limit_bounds() -> None:
    query = _schema(KnowledgeQuery)
    limit = _schema(KnowledgeLimit)

    assert query["minLength"] == 1
    assert query["maxLength"] == 2000
    assert limit["minimum"] == 1
    assert limit["maximum"] == 10


def test_service_diagnostic_schema_exposes_only_real_application_targets() -> None:
    target = _schema(ServiceTargetHostId)
    timeout = _schema(DiagnosticTimeout)
    path = _schema(HttpPath)

    assert set(target["enum"]) == {
        "api-gateway",
        "processing-service",
        "data-service",
    }
    assert timeout["minimum"] == 0.2
    assert timeout["maximum"] == 5.0
    assert path["minLength"] == 1
    assert path["maxLength"] == 256
    assert path["pattern"] == "^/"


def test_icmp_tool_schema_keeps_probe_small_and_bounded() -> None:
    count = _schema(PingCount)
    timeout = _schema(PingTimeoutSeconds)

    assert count["minimum"] == 1
    assert count["maximum"] == 4
    assert timeout["minimum"] == 1
    assert timeout["maximum"] == 2


def test_service_internal_ports_are_authoritative_container_ports() -> None:
    assert SERVICE_INTERNAL_ENDPOINTS == {
        "api-gateway": {"port": 5000, "health_path": "/health"},
        "processing-service": {"port": 8000, "health_path": "/health"},
        "data-service": {"port": 8000, "health_path": "/health"},
    }
    assert service_internal_port("api-gateway") == 5000
    assert service_internal_port("processing-service") == 8000
    assert service_internal_port("data-service") == 8000
