import os
from typing import Literal

import httpx
from mcp.server import MCPServer


HostId = Literal[
    "machine-01",
    "machine-02",
    "machine-03",
    "machine-04",
    "machine-05",
]

MetricName = Literal["cpu", "memory"]
LogLevel = Literal[
    "ALL",
    "DEBUG",
    "INFO",
    "WARN",
    "ERROR",
]

LogSource = Literal[
    "all",
    "application",
    "system",
]


OPENSEARCH_URL = os.getenv(
    "OPENSEARCH_URL",
    "http://opensearch:9200",
).rstrip("/")


METRICS = {
    "cpu": {
        "measurement": "cpu",
        "field": "cpu.usage_active",
        "unit": "percent",
    },
    "memory": {
        "measurement": "mem",
        "field": "mem.used_percent",
        "unit": "percent",
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


def register_opensearch_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    async def get_metrics(host_id: HostId, metric: MetricName, minutes: int = 15) -> dict:
        """
        Retrieve recent monitoring metrics for one monitored machine.

        The tool is read-only.

        Supported metrics:
        - cpu: active CPU usage percentage
        - memory: used memory percentage
        """

        if minutes < 1 or minutes > 120:
            raise ValueError(
                "minutes must be between 1 and 120"
            )

        metric_config = METRICS[metric]

        measurement = metric_config["measurement"]
        field = metric_config["field"]
        unit = metric_config["unit"]

        index_pattern = f"metrics-{host_id}-*"

        query = {
            "size": 1,
            "_source": [
                "@timestamp",
                field,
            ],
            "sort": [
                {
                    "@timestamp": {
                        "order": "desc"
                    }
                }
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
                        {
                            "term": {
                                "measurement_name": measurement
                            }
                        },
                        {
                            "exists": {
                                "field": field
                            }
                        },
                    ]
                }
            },
            "aggs": {
                "statistics": {
                    "stats": {
                        "field": field
                    }
                },
                "timeline": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": "1m",
                        "min_doc_count": 1,
                    },
                    "aggs": {
                        "value": {
                            "avg": {
                                "field": field
                            }
                        }
                    },
                },
            },
        }

        url = (
            f"{OPENSEARCH_URL}/"
            f"{index_pattern}/_search"
            f"?ignore_unavailable=true"
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=query,
            )

            response.raise_for_status()
            result = response.json()

        stats = result["aggregations"]["statistics"]

        hits = result.get("hits", {}).get("hits", [])

        latest_timestamp = None
        latest_value = None

        if hits:
            source = hits[0].get("_source", {})

            latest_timestamp = source.get("@timestamp")
            latest_value = _nested_value(
                source,
                field,
            )

        timeline = []

        for bucket in result["aggregations"]["timeline"]["buckets"]:
            value = bucket["value"]["value"]

            timeline.append(
                {
                    "timestamp": bucket["key_as_string"],
                    "value": (
                        round(value, 2)
                        if value is not None
                        else None
                    ),
                }
            )

        return {
            "status": "ok",
            "host_id": host_id,
            "metric": metric,
            "field": field,
            "unit": unit,
            "window_minutes": minutes,
            "samples": stats["count"],
            "summary": {
                "latest_timestamp": latest_timestamp,
                "latest": (
                    round(latest_value, 2)
                    if isinstance(latest_value, (int, float))
                    else latest_value
                ),
                "average": (
                    round(stats["avg"], 2)
                    if stats["avg"] is not None
                    else None
                ),
                "minimum": (
                    round(stats["min"], 2)
                    if stats["min"] is not None
                    else None
                ),
                "maximum": (
                    round(stats["max"], 2)
                    if stats["max"] is not None
                    else None
                ),
            },
            "timeline": timeline,
        }

    @mcp.tool()
    async def get_logs(
            host_id: HostId,
            minutes: int = 15,
            level: LogLevel = "ALL",
            source: LogSource = "all",
            limit: int = 30,
    ) -> dict:
        """
        Retrieve recent logs for one monitored machine.

        The tool is read-only.

        Logs can be filtered by:
        - level: DEBUG, INFO, WARN, ERROR or ALL
        - source: application, system or all
        """

        if minutes < 1 or minutes > 120:
            raise ValueError(
                "minutes must be between 1 and 120"
            )

        if limit < 1 or limit > 100:
            raise ValueError(
                "limit must be between 1 and 100"
            )

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
            {
                "term": {
                    "host_id": host_id
                }
            },
        ]

        if level != "ALL":
            filters.append(
                {
                    "term": {
                        "level": level
                    }
                }
            )

        if source != "all":
            filters.append(
                {
                    "term": {
                        "log_source": source
                    }
                }
            )

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
            "sort": [
                {
                    "@timestamp": {
                        "order": "desc"
                    }
                }
            ],
            "query": {
                "bool": {
                    "filter": filters
                }
            },
            "aggs": {
                "levels": {
                    "terms": {
                        "field": "level",
                        "size": 10,
                    }
                },
                "sources": {
                    "terms": {
                        "field": "log_source",
                        "size": 10,
                    }
                },
            },
        }

        url = (
            f"{OPENSEARCH_URL}/"
            f"{index_pattern}/_search"
            f"?ignore_unavailable=true"
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=query,
            )

            response.raise_for_status()
            result = response.json()

        total = (
            result
            .get("hits", {})
            .get("total", {})
            .get("value", 0)
        )

        logs = []

        for hit in result.get("hits", {}).get("hits", []):
            source_document = hit.get("_source", {})

            logs.append(
                {
                    "timestamp": source_document.get("@timestamp"),
                    "log_timestamp": source_document.get("timestamp"),
                    "source": source_document.get("log_source"),
                    "level": source_document.get("level"),
                    "message": source_document.get("message"),
                    "service": source_document.get("service"),
                    "component": source_document.get("component"),
                    "event_type": source_document.get("event_type"),
                    "latency_ms": source_document.get("latency_ms"),
                    "error_code": source_document.get("error_code"),
                    "uptime_seconds": source_document.get(
                        "uptime_seconds"
                    ),
                    "load_average": source_document.get(
                        "load_average"
                    ),
                }
            )

        level_counts = {
            bucket["key"]: bucket["doc_count"]
            for bucket in (
                result
                .get("aggregations", {})
                .get("levels", {})
                .get("buckets", [])
            )
        }

        source_counts = {
            bucket["key"]: bucket["doc_count"]
            for bucket in (
                result
                .get("aggregations", {})
                .get("sources", {})
                .get("buckets", [])
            )
        }

        return {
            "status": "ok",
            "host_id": host_id,
            "window_minutes": minutes,
            "filters": {
                "level": level,
                "source": source,
            },
            "total_matches": total,
            "returned_logs": len(logs),
            "level_counts": level_counts,
            "source_counts": source_counts,
            "logs": logs,
        }

    @mcp.tool()
    async def search_logs(
            host_id: HostId,
            query_text: str,
            minutes: int = 30,
            limit: int = 20,
    ) -> dict:
        """
        Search recent logs of one monitored machine using full-text search.

        The tool is read-only.

        It searches primarily in log messages and also considers
        event type, service and component information.
        """

        if not query_text or not query_text.strip():
            raise ValueError(
                "query_text must not be empty"
            )

        if minutes < 1 or minutes > 120:
            raise ValueError(
                "minutes must be between 1 and 120"
            )

        if limit < 1 or limit > 100:
            raise ValueError(
                "limit must be between 1 and 100"
            )

        index_pattern = f"logs-{host_id}-*"

        search_query = {
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
                        {
                            "term": {
                                "host_id": host_id
                            }
                        },
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
                {
                    "_score": {
                        "order": "desc"
                    }
                },
                {
                    "@timestamp": {
                        "order": "desc"
                    }
                },
            ],
        }

        url = (
            f"{OPENSEARCH_URL}/"
            f"{index_pattern}/_search"
            f"?ignore_unavailable=true"
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=search_query,
            )

            response.raise_for_status()
            result = response.json()

        total_matches = (
            result
            .get("hits", {})
            .get("total", {})
            .get("value", 0)
        )

        logs = []

        for hit in result.get("hits", {}).get("hits", []):
            document = hit.get("_source", {})

            logs.append(
                {
                    "score": round(
                        hit.get("_score", 0.0) or 0.0,
                        4,
                        ),
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