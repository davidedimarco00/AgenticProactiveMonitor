import os
from typing import Annotated, Literal

import httpx
from mcp.server import MCPServer
from pydantic import Field


HostId = Literal[
    "traffic-generator",
    "api-gateway",
    "processing-service",
    "data-service",
    "worker-service",
]

TransportTargetHostId = Literal[
    "api-gateway",
    "processing-service",
    "data-service",
]

MetricName = Literal["cpu", "memory"]
LogLevel = Literal["ALL", "DEBUG", "INFO", "WARN", "ERROR"]
LogSource = Literal["all", "application", "system"]
TimeWindowMinutes = Annotated[int, Field(ge=1, le=120)]
ResultLimit = Annotated[int, Field(ge=1, le=100)]
SearchText = Annotated[str, Field(min_length=1)]

OPENSEARCH_URL = os.getenv(
    "OPENSEARCH_URL",
    "http://opensearch:9200",
).rstrip("/")

# These definitions intentionally match the SINGLE_ENTITY CPU/RAM detector
# inputs created by infrastructure/opensearch/init/create-anomaly-detectors.sh.
# A specialist investigating a detector therefore reads the same telemetry
# quantity that OpenSearch marked as anomalous instead of silently switching to
# host-level inputs.cpu / inputs.mem measurements.
METRICS = {
    "cpu": {
        "measurement": "docker_container_cpu",
        "field": "docker_container_cpu.usage_percent",
        "unit": "percent",
        "scope": "container",
    },
    "memory": {
        "measurement": "docker_container_mem",
        "field": "docker_container_mem.usage_percent",
        "unit": "percent",
        "scope": "container",
    },
}

TRANSPORT_LATENCY_METRIC = {
    "measurement": "network_transport_latency",
    "field": "network_transport_latency.response_time",
    "unit": "seconds",
    "scope": "service_path",
}


def _nested_value(document: dict, field: str):
    value = document
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
        if value is None:
            return None
    return value


def _milliseconds(value):
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) * 1000.0, 2)


def register_opensearch_tools(mcp: MCPServer) -> None:
    @mcp.tool()
    async def get_metrics(
        host_id: HostId,
        metric: MetricName,
        minutes: TimeWindowMinutes = 15,
    ) -> dict:
        """
        Retrieve recent detector-aligned container CPU or memory metrics for one
        monitored service. These are the same telemetry fields used by the
        SINGLE_ENTITY CPU/RAM OpenSearch anomaly detectors. The tool is read-only.
        """
        if minutes < 1 or minutes > 120:
            raise ValueError("minutes must be between 1 and 120")

        metric_config = METRICS[metric]
        measurement = metric_config["measurement"]
        field = metric_config["field"]
        unit = metric_config["unit"]
        scope = metric_config["scope"]
        index_pattern = f"metrics-{host_id}-*"

        query = {
            "size": 1,
            "_source": ["@timestamp", field],
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": f"now-{minutes}m",
                                    "lte": "now",
                                }
                            }
                        },
                        {"term": {"measurement_name": measurement}},
                        {"exists": {"field": field}},
                    ]
                }
            },
            "aggs": {
                "statistics": {"stats": {"field": field}},
                "timeline": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": "1m",
                        "min_doc_count": 1,
                    },
                    "aggs": {"value": {"avg": {"field": field}}},
                },
            },
        }

        url = f"{OPENSEARCH_URL}/{index_pattern}/_search?ignore_unavailable=true"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=query)
            response.raise_for_status()
            result = response.json()

        stats = result["aggregations"]["statistics"]
        hits = result.get("hits", {}).get("hits", [])

        latest_timestamp = None
        latest_value = None
        if hits:
            source = hits[0].get("_source", {})
            latest_timestamp = source.get("@timestamp")
            latest_value = _nested_value(source, field)

        timeline = []
        for bucket in result["aggregations"]["timeline"]["buckets"]:
            value = bucket["value"]["value"]
            timeline.append(
                {
                    "timestamp": bucket["key_as_string"],
                    "value": round(value, 2) if value is not None else None,
                }
            )

        return {
            "status": "ok",
            "host_id": host_id,
            "metric": metric,
            "measurement_name": measurement,
            "field": field,
            "unit": unit,
            "scope": scope,
            "detector_aligned": True,
            "window_minutes": minutes,
            "samples": stats["count"],
            "summary": {
                "latest_timestamp": latest_timestamp,
                "latest": (
                    round(latest_value, 2)
                    if isinstance(latest_value, (int, float))
                    else latest_value
                ),
                "average": round(stats["avg"], 2) if stats["avg"] is not None else None,
                "minimum": round(stats["min"], 2) if stats["min"] is not None else None,
                "maximum": round(stats["max"], 2) if stats["max"] is not None else None,
            },
            "timeline": timeline,
        }

    @mcp.tool()
    async def get_transport_latency(
        host_id: HostId,
        target_host: TransportTargetHostId,
        minutes: TimeWindowMinutes = 15,
    ) -> dict:
        """
        Retrieve detector-aligned Layer-4 transport latency for one monitored
        source-to-target service path from OpenSearch. The returned response_time
        field is the same field used by the SINGLE_ENTITY NETLAT detectors.
        Values are returned in detector-native seconds and also converted to
        milliseconds for direct diagnostic interpretation. The tool is read-only.
        """
        if minutes < 1 or minutes > 120:
            raise ValueError("minutes must be between 1 and 120")
        if host_id == target_host:
            raise ValueError("host_id and target_host must identify different services")

        measurement = TRANSPORT_LATENCY_METRIC["measurement"]
        field = TRANSPORT_LATENCY_METRIC["field"]
        unit = TRANSPORT_LATENCY_METRIC["unit"]
        scope = TRANSPORT_LATENCY_METRIC["scope"]
        index_pattern = f"metrics-{host_id}-*"

        query = {
            "size": 1,
            "_source": ["@timestamp", "network_target", field],
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": f"now-{minutes}m",
                                    "lte": "now",
                                }
                            }
                        },
                        {"term": {"measurement_name": measurement}},
                        {"term": {"network_target": target_host}},
                        {"exists": {"field": field}},
                    ]
                }
            },
            "aggs": {
                "statistics": {"stats": {"field": field}},
                "timeline": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": "1m",
                        "min_doc_count": 1,
                    },
                    "aggs": {"value": {"avg": {"field": field}}},
                },
            },
        }

        url = f"{OPENSEARCH_URL}/{index_pattern}/_search?ignore_unavailable=true"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=query)
            response.raise_for_status()
            result = response.json()

        stats = result["aggregations"]["statistics"]
        hits = result.get("hits", {}).get("hits", [])

        latest_timestamp = None
        latest_target = None
        latest_value = None
        if hits:
            source = hits[0].get("_source", {})
            latest_timestamp = source.get("@timestamp")
            latest_target = source.get("network_target")
            latest_value = _nested_value(source, field)

        timeline = []
        for bucket in result["aggregations"]["timeline"]["buckets"]:
            value = bucket["value"]["value"]
            rounded = round(value, 6) if value is not None else None
            timeline.append(
                {
                    "timestamp": bucket["key_as_string"],
                    "value_seconds": rounded,
                    "value_ms": _milliseconds(value),
                }
            )

        return {
            "status": "ok",
            "host_id": host_id,
            "target_host": target_host,
            "observed_network_target": latest_target,
            "metric": "transport_latency",
            "measurement_name": measurement,
            "field": field,
            "unit": unit,
            "scope": scope,
            "detector_aligned": True,
            "window_minutes": minutes,
            "samples": stats["count"],
            "summary": {
                "latest_timestamp": latest_timestamp,
                "latest_seconds": (
                    round(latest_value, 6)
                    if isinstance(latest_value, (int, float))
                    else latest_value
                ),
                "latest_ms": _milliseconds(latest_value),
                "average_seconds": (
                    round(stats["avg"], 6) if stats["avg"] is not None else None
                ),
                "average_ms": _milliseconds(stats["avg"]),
                "minimum_seconds": (
                    round(stats["min"], 6) if stats["min"] is not None else None
                ),
                "minimum_ms": _milliseconds(stats["min"]),
                "maximum_seconds": (
                    round(stats["max"], 6) if stats["max"] is not None else None
                ),
                "maximum_ms": _milliseconds(stats["max"]),
            },
            "timeline": timeline,
        }

    @mcp.tool()
    async def get_logs(
        host_id: HostId,
        minutes: TimeWindowMinutes = 15,
        level: LogLevel = "ALL",
        source: LogSource = "all",
        limit: ResultLimit = 30,
    ) -> dict:
        """
        Retrieve recent application or system logs for one monitored service.
        The tool is read-only.
        """
        if minutes < 1 or minutes > 120:
            raise ValueError("minutes must be between 1 and 120")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        index_pattern = f"logs-{host_id}-*"
        filters = [
            {
                "range": {
                    "@timestamp": {
                        "gte": f"now-{minutes}m",
                        "lte": "now",
                    }
                }
            },
            {"term": {"host_id": host_id}},
        ]

        if level != "ALL":
            filters.append({"term": {"level": level}})
        if source != "all":
            filters.append({"term": {"log_source": source}})

        query = {
            "size": limit,
            "track_total_hits": True,
            "_source": [
                "@timestamp",
                "timestamp",
                "host_id",
                "machine_role",
                "log_source",
                "level",
                "message",
                "service",
                "component",
                "event_type",
                "latency_ms",
                "error_code",
                "uptime_seconds",
                "load_average",
            ],
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"filter": filters}},
            "aggs": {
                "levels": {"terms": {"field": "level", "size": 10}},
                "sources": {"terms": {"field": "log_source", "size": 10}},
            },
        }

        url = f"{OPENSEARCH_URL}/{index_pattern}/_search?ignore_unavailable=true"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=query)
            response.raise_for_status()
            result = response.json()

        total = result.get("hits", {}).get("total", {}).get("value", 0)
        logs = []
        for hit in result.get("hits", {}).get("hits", []):
            document = hit.get("_source", {})
            logs.append(
                {
                    "timestamp": document.get("@timestamp"),
                    "log_timestamp": document.get("timestamp"),
                    "source": document.get("log_source"),
                    "level": document.get("level"),
                    "message": document.get("message"),
                    "service": document.get("service"),
                    "component": document.get("component"),
                    "event_type": document.get("event_type"),
                    "latency_ms": document.get("latency_ms"),
                    "error_code": document.get("error_code"),
                    "uptime_seconds": document.get("uptime_seconds"),
                    "load_average": document.get("load_average"),
                }
            )

        level_counts = {
            bucket["key"]: bucket["doc_count"]
            for bucket in result.get("aggregations", {})
            .get("levels", {})
            .get("buckets", [])
        }
        source_counts = {
            bucket["key"]: bucket["doc_count"]
            for bucket in result.get("aggregations", {})
            .get("sources", {})
            .get("buckets", [])
        }

        return {
            "status": "ok",
            "host_id": host_id,
            "window_minutes": minutes,
            "filters": {"level": level, "source": source},
            "total_matches": total,
            "returned_logs": len(logs),
            "level_counts": level_counts,
            "source_counts": source_counts,
            "logs": logs,
        }

    @mcp.tool()
    async def search_logs(
        host_id: HostId,
        query_text: SearchText,
        minutes: TimeWindowMinutes = 30,
        limit: ResultLimit = 20,
    ) -> dict:
        """
        Full-text search recent logs for one monitored service.
        The tool is read-only.
        """
        if not query_text or not query_text.strip():
            raise ValueError("query_text must not be empty")
        if minutes < 1 or minutes > 120:
            raise ValueError("minutes must be between 1 and 120")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        index_pattern = f"logs-{host_id}-*"
        query = {
            "size": limit,
            "track_total_hits": True,
            "_source": [
                "@timestamp",
                "timestamp",
                "host_id",
                "machine_role",
                "log_source",
                "level",
                "message",
                "service",
                "component",
                "event_type",
                "latency_ms",
                "error_code",
            ],
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": f"now-{minutes}m",
                                    "lte": "now",
                                }
                            }
                        },
                        {"term": {"host_id": host_id}},
                    ],
                    "must": [
                        {
                            "multi_match": {
                                "query": query_text,
                                "fields": [
                                    "message^3",
                                    "event_type",
                                    "service",
                                    "component",
                                ],
                                "type": "best_fields",
                                "operator": "or",
                            }
                        }
                    ],
                }
            },
            "sort": [
                {"_score": {"order": "desc"}},
                {"@timestamp": {"order": "desc"}},
            ],
        }

        url = f"{OPENSEARCH_URL}/{index_pattern}/_search?ignore_unavailable=true"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=query)
            response.raise_for_status()
            result = response.json()

        total_matches = result.get("hits", {}).get("total", {}).get("value", 0)
        logs = []
        for hit in result.get("hits", {}).get("hits", []):
            document = hit.get("_source", {})
            logs.append(
                {
                    "score": round(hit.get("_score", 0.0) or 0.0, 4),
                    "timestamp": document.get("@timestamp"),
                    "source": document.get("log_source"),
                    "level": document.get("level"),
                    "message": document.get("message"),
                    "service": document.get("service"),
                    "component": document.get("component"),
                    "event_type": document.get("event_type"),
                    "latency_ms": document.get("latency_ms"),
                    "error_code": document.get("error_code"),
                }
            )

        return {
            "status": "ok",
            "host_id": host_id,
            "query": query_text,
            "window_minutes": minutes,
            "total_matches": total_matches,
            "returned_logs": len(logs),
            "logs": logs,
        }
