from pydantic import TypeAdapter

from tools.docker_tools import ConnectionLimit, ProcessLimit
from tools.opensearch_tools import ResultLimit, TimeWindowMinutes
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


def test_qdrant_tool_schema_publishes_query_and_limit_bounds() -> None:
    query = _schema(KnowledgeQuery)
    limit = _schema(KnowledgeLimit)

    assert query["minLength"] == 1
    assert query["maxLength"] == 2000
    assert limit["minimum"] == 1
    assert limit["maximum"] == 10
