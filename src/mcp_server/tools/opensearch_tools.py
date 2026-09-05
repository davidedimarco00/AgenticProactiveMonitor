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

MetricName = Literal["cpu", "memory", "network_transport_latency"]
LogLevel = Literal["ALL", "DEBUG", "INFO", "WARN", "ERROR"]
LogSource = Literal["all", "application", "system"]
TimeWindowMinutes = Annotated[int, Field(ge=1, le=120)]
ResultLimit = Annotated[int, Field(ge=1, le=100)]
SearchText = Annotated[str, Field(min_length=1)]

OPENSEARCH_URL = os.getenv(
    "OPENSEARCH_URL",
    "http://opensearch:9200",
).rstrip("/")

# These definitions intentionally match the fields used by the implemented
# SINGLE_ENTITY OpenSearch detectors. A specialist therefore reads the same
# telemetry quantity that the detector marked as anomalous.
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
    "network_transport_latency": {
        "measurement": "network_transport_latency",
        "field": "network_transport_latency.response_time",
        "unit": "seconds",
        "scope": "service_path",
    },
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
        target_host: TransportTargetHostId | None = None,
        minutes: TimeWindowMinutes = 15,
    ) -> dict:
        """
        Retrieve recent detector-aligned telemetry from OpenSearch.

        CPU and memory read the exact container fields used by the SINGLE_ENTITY
        CPU/RAM detectors. network_transport_latency reads the exact Layer-4
        response_time field used by NETLAT and additionally requires target_host
        so the requested source-to-target path is preserved. The tool is read-only.
        """
        if minutes < 1 or minutes > 120:
            raise ValueError("minutes must be between 1 and 120")

        is_transport = metric == "network_transport_latency"
        if is_transport and not target_host:
            raise ValueError(
                "target_host is required when metric is network_transport_latency"
            )
        if not is_transport and target_host is not None:
            raise ValueError(
                "target_host is only valid when metric is network_transport_latency"
            )
        if target_host is not None and host_id == target_host:
            raise ValueError("host_id and target_host must identify different services")

        metric_config = METRICS[metric]
        measurement = metric_config["measurement"]
        field = metric_config["field"]
        unit = metric_config["unit"]
        scope = metric_config["scope"]
        index_pattern = f"metrics-{host_id}-*"

        filters = [
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
        source_fields = ["@timestamp", field]
        if is_transport:
            filters.append({"term": {"network_target": target_host}})
            source_fields.append("network_target")

        query = {
            "size": 1,
            "_source": source_fields,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"filter": filters}},
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
        observed_network_target = None
        if hits:
            source = hits[0].get("_source", {})
            latest_timestamp = source.get("@timestamp")
            latest_value = _nested_value(source, field)
            observed_network_target = source.get("network_target")

        timeline = []
        for bucket in result["aggregations"]["timeline"]["buckets"]:
            value = bucket["value"]["value"]
            item = {
                "timestamp": bucket["key_as_string"],
                "value": round(value, 6) if value is not None else None,
            }
            if is_transport:
                item["value_ms"] = _milliseconds(value)
            timeline.append(item)

        summary = {
            "latest_timestamp": latest_timestamp,
            "latest": (
                round(latest_value, 6)
                if isinstance(latest_value, (int, float))
                else latest_value
            ),
            "average": round(stats["avg"], 6) if stats["avg"] is not None else None,
            "minimum": round(stats["min"], 6) if stats["min"] is not None else None,
            "maximum": round(stats["max"], 6) if stats["max"] is not None else None,
        }
        if is_transport:
            summary.update(
                {
                    "latest_ms": _milliseconds(latest_value),
                    "average_ms": _milliseconds(stats["avg"]),
                    "minimum_ms": _milliseconds(stats["min"]),
                    "maximum_ms": _milliseconds(stats["max"]),
                }
            )

        return {
            "status": "ok",
            "host_id": host_id,
            "target_host": target_host,
            "observed_network_target": observed_network_target,
            "metric": metric,
            "measurement_name": measurement,
            "field": field,
            "unit": unit,
            "scope": scope,
            "detector_aligned": True,
            "window_minutes": minutes,
            "samples": stats["count"],
            "summary": summary,
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
