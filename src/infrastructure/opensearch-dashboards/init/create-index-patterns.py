#!/usr/bin/env python3
"""Create OpenSearch Dashboards data views with fresh field metadata.

Creating a saved object with only title/timeFieldName can leave an empty or stale
field cache in Discover. This initializer reads _field_caps after real telemetry
exists and stores the complete fields metadata in each data view.
"""

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
DASHBOARDS_URL = os.getenv(
    "DASHBOARDS_URL", "http://opensearch-dashboards:5601"
).rstrip("/")
WAIT_SECONDS = int(os.getenv("DASHBOARDS_WAIT_SECONDS", "180"))


class InitializerError(RuntimeError):
    pass


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise InitializerError(
            f"HTTP {exc.code} for {method} {base_url}{path}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise InitializerError(f"Unable to reach {base_url}: {exc}") from exc


def wait_for_endpoint(name: str, base_url: str, path: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            request_json(base_url, "GET", path)
            print(f"{name} is available.", flush=True)
            return
        except InitializerError:
            print(f"Waiting for {name}...", flush=True)
            time.sleep(3)
    raise InitializerError(f"{name} did not become available within {WAIT_SECONDS}s")


def wait_for_documents(index_pattern: str) -> None:
    encoded_pattern = urllib.parse.quote(index_pattern, safe="-.*_")
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            response = request_json(
                OPENSEARCH_URL, "GET", f"/{encoded_pattern}/_count"
            )
            count = int(response.get("count", 0))
            if count > 0:
                print(
                    f"Found {count} documents matching {index_pattern}.", flush=True
                )
                return
        except InitializerError:
            pass

        print(f"Waiting for documents matching {index_pattern}...", flush=True)
        time.sleep(3)

    raise InitializerError(
        f"No documents matching {index_pattern} arrived within {WAIT_SECONDS}s"
    )


def dashboards_type(es_types: set[str]) -> str:
    if not es_types:
        return "unknown"
    if len(es_types) > 1:
        compatible_numeric = {
            "byte",
            "short",
            "integer",
            "long",
            "unsigned_long",
            "half_float",
            "float",
            "double",
            "scaled_float",
        }
        if es_types.issubset(compatible_numeric):
            return "number"
        return "conflict"

    es_type = next(iter(es_types))
    if es_type in {"keyword", "constant_keyword", "wildcard", "text", "match_only_text"}:
        return "string"
    if es_type in {
        "byte",
        "short",
        "integer",
        "long",
        "unsigned_long",
        "half_float",
        "float",
        "double",
        "scaled_float",
    }:
        return "number"
    if es_type in {"date", "date_nanos"}:
        return "date"
    if es_type == "boolean":
        return "boolean"
    if es_type in {"object", "nested", "flattened"}:
        return "object"
    if es_type in {"geo_point", "geo_shape"}:
        return es_type
    if es_type in {"ip", "version"}:
        return "string"
    return es_type


def build_fields(index_pattern: str) -> list[dict[str, Any]]:
    encoded_pattern = urllib.parse.quote(index_pattern, safe="-.*_")
    response = request_json(
        OPENSEARCH_URL,
        "GET",
        f"/{encoded_pattern}/_field_caps?fields=*&include_unmapped=false",
    )
    capabilities = response.get("fields", {})
    fields: list[dict[str, Any]] = []

    for field_name in sorted(capabilities):
        type_definitions = capabilities[field_name]
        es_types = set(type_definitions)
        searchable = any(
            bool(definition.get("searchable", False))
            for definition in type_definitions.values()
        )
        aggregatable = any(
            bool(definition.get("aggregatable", False))
            for definition in type_definitions.values()
        )
        visual_type = dashboards_type(es_types)

        fields.append(
            {
                "name": field_name,
                "type": visual_type,
                "esTypes": sorted(es_types),
                "count": 0,
                "scripted": False,
                "searchable": searchable,
                "aggregatable": aggregatable,
                "readFromDocValues": aggregatable
                and visual_type not in {"object", "conflict", "unknown"},
            }
        )

    if not fields:
        raise InitializerError(f"OpenSearch returned no fields for {index_pattern}")

    return fields


def create_or_update_data_view(
    object_id: str,
    index_pattern: str,
    required_fields: tuple[str, ...] = (),
) -> None:
    fields = build_fields(index_pattern)
    field_names = {field["name"] for field in fields}
    missing = sorted(set(required_fields) - field_names)
    if missing:
        raise InitializerError(
            f"Cannot create {index_pattern} data view; missing fields: {', '.join(missing)}"
        )

    payload = {
        "attributes": {
            "title": index_pattern,
            "timeFieldName": "@timestamp",
            # Dashboards stores this attribute as a JSON-encoded string.
            "fields": json.dumps(fields, separators=(",", ":")),
        }
    }
    xsrf_headers = {"osd-xsrf": "true", "kbn-xsrf": "true"}

    request_json(
        DASHBOARDS_URL,
        "POST",
        f"/api/saved_objects/index-pattern/{object_id}?overwrite=true",
        payload,
        xsrf_headers,
    )

    saved_object = request_json(
        DASHBOARDS_URL,
        "GET",
        f"/api/saved_objects/index-pattern/{object_id}",
        headers=xsrf_headers,
    )
    attributes = saved_object.get("attributes", {})
    persisted_fields_raw = attributes.get("fields", "[]")
    try:
        persisted_fields = json.loads(persisted_fields_raw)
    except json.JSONDecodeError as exc:
        raise InitializerError(
            f"Dashboards persisted invalid field metadata for {index_pattern}"
        ) from exc

    persisted_names = {field.get("name") for field in persisted_fields}
    missing_after_write = sorted(set(required_fields) - persisted_names)
    if attributes.get("title") != index_pattern or missing_after_write:
        raise InitializerError(
            f"Dashboards did not persist the complete {index_pattern} data view"
        )

    print(
        f"Created or updated {index_pattern} data view with {len(fields)} fields.",
        flush=True,
    )


def main() -> int:
    try:
        wait_for_endpoint("OpenSearch", OPENSEARCH_URL, "/_cluster/health")
        wait_for_documents("metrics-*")
        wait_for_documents("logs-*")
        wait_for_endpoint("OpenSearch Dashboards", DASHBOARDS_URL, "/api/status")

        create_or_update_data_view(
            "metrics-index-pattern",
            "metrics-*",
            required_fields=(
                "@timestamp",
                "measurement_name",
                "tag.host_id",
                "cpu.usage_active",
                "mem.used_percent",
            ),
        )
        create_or_update_data_view(
            "logs-index-pattern",
            "logs-*",
            required_fields=("@timestamp", "host_id", "level", "message"),
        )

        print("OpenSearch Dashboards data views are ready.", flush=True)
        return 0
    except (InitializerError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
