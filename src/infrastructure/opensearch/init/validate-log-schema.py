#!/usr/bin/env python3
"""Validate Fluent Bit application and system log documents in OpenSearch."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200").rstrip("/")
WAIT_SECONDS = int(os.getenv("TELEMETRY_SCHEMA_WAIT_SECONDS", "180"))
LOOKBACK = os.getenv("TELEMETRY_SCHEMA_LOOKBACK", "now-10m")
EXPECTED_HOSTS = tuple(
    host.strip()
    for host in os.getenv(
        "TELEMETRY_EXPECTED_HOSTS",
        "machine-01,machine-02,machine-03,machine-04,machine-05",
    ).split(",")
    if host.strip()
)


class ValidationError(RuntimeError):
    pass


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{OPENSEARCH_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValidationError(
            f"OpenSearch returned HTTP {exc.code} for {method} {path}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ValidationError(f"Unable to reach OpenSearch: {exc}") from exc


def field_caps(field_names: list[str]) -> dict[str, Any]:
    fields = urllib.parse.quote(",".join(field_names), safe=".,@*")
    response = request_json(
        "GET", f"/logs-*/_field_caps?fields={fields}&include_unmapped=true"
    )
    return response.get("fields", {})


def require_type(
    capabilities: dict[str, Any],
    field_name: str,
    accepted_types: set[str],
    *,
    require_aggregatable: bool,
) -> None:
    definitions = capabilities.get(field_name)
    if not definitions:
        raise ValidationError(f"Required log field {field_name!r} is missing")

    matching_types = accepted_types.intersection(definitions)
    if not matching_types:
        raise ValidationError(
            f"Log field {field_name!r} has type(s) {sorted(definitions)}; "
            f"expected {sorted(accepted_types)}"
        )

    for field_type in matching_types:
        definition = definitions[field_type]
        if not definition.get("searchable", False):
            raise ValidationError(f"Log field {field_name!r} is not searchable")
        if require_aggregatable and not definition.get("aggregatable", False):
            raise ValidationError(f"Log field {field_name!r} is not aggregatable")


def validate_field_capabilities() -> None:
    capabilities = field_caps(
        [
            "@timestamp",
            "timestamp",
            "host_id",
            "machine_role",
            "monitored_by",
            "log_source",
            "level",
            "message",
        ]
    )
    require_type(capabilities, "@timestamp", {"date", "date_nanos"}, require_aggregatable=True)
    require_type(capabilities, "timestamp", {"date", "date_nanos"}, require_aggregatable=True)
    require_type(capabilities, "host_id", {"keyword"}, require_aggregatable=True)
    require_type(capabilities, "machine_role", {"keyword"}, require_aggregatable=True)
    require_type(capabilities, "monitored_by", {"keyword"}, require_aggregatable=True)
    require_type(capabilities, "log_source", {"keyword", "text"}, require_aggregatable=False)
    require_type(capabilities, "level", {"keyword"}, require_aggregatable=True)
    require_type(capabilities, "message", {"text", "keyword"}, require_aggregatable=False)
    print("Log field capabilities match the Fluent Bit schema.", flush=True)


def log_query(log_source: str) -> dict[str, Any]:
    return {
        "size": 1,
        "track_total_hits": True,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"log_source.keyword": log_source}},
                    {"term": {"monitored_by": "fluent-bit"}},
                    {"range": {"@timestamp": {"gte": LOOKBACK}}},
                ]
            }
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
        "aggs": {
            "hosts": {
                "terms": {"field": "host_id", "size": max(20, len(EXPECTED_HOSTS))}
            }
        },
    }


def total_hits(response: dict[str, Any]) -> int:
    total = response.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        return int(total.get("value", 0))
    return int(total)


def validate_source(log_source: str) -> bool:
    response = request_json("POST", "/logs-*/_search", log_query(log_source))
    hits = response.get("hits", {}).get("hits", [])
    buckets = response.get("aggregations", {}).get("hosts", {}).get("buckets", [])
    observed_hosts = {bucket.get("key") for bucket in buckets}
    missing_hosts = sorted(set(EXPECTED_HOSTS) - observed_hosts)

    if total_hits(response) <= 0 or not hits:
        print(f"Waiting for recent {log_source} logs...", flush=True)
        return False
    if missing_hosts:
        print(
            f"Waiting for {log_source} logs from: {', '.join(missing_hosts)}",
            flush=True,
        )
        return False

    source = hits[0].get("_source", {})
    for field_name in (
        "@timestamp",
        "timestamp",
        "host_id",
        "machine_role",
        "monitored_by",
        "log_source",
        "level",
        "message",
    ):
        if field_name not in source:
            raise ValidationError(
                f"Latest {log_source} log does not contain {field_name}"
            )

    if source.get("log_source") != log_source:
        raise ValidationError(
            f"Latest log has log_source={source.get('log_source')!r}; "
            f"expected {log_source!r}"
        )
    if source.get("host_id") not in EXPECTED_HOSTS:
        raise ValidationError(
            f"Latest {log_source} log has invalid host_id={source.get('host_id')!r}"
        )

    print(
        f"{log_source} logs: {total_hits(response)} recent documents; "
        f"hosts={','.join(sorted(observed_hosts))}",
        flush=True,
    )
    return True


def main() -> int:
    try:
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                validate_field_capabilities()
                if validate_source("application") and validate_source("system"):
                    print(
                        "Fluent Bit log schema validation completed successfully.",
                        flush=True,
                    )
                    return 0
            except ValidationError as exc:
                print(f"Logs are not ready: {exc}", flush=True)
            time.sleep(5)

        raise ValidationError(
            f"Logs did not satisfy the schema contract within {WAIT_SECONDS}s"
        )
    except (ValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
