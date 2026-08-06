#!/usr/bin/env python3
"""Validate the telemetry contract used by OpenSearch and anomaly detectors.

The script does not create or transform telemetry. It waits for real Telegraf
samples and verifies the exact document structure, mappings, field capabilities,
and host coverage required by the CPU and memory detectors.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
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


@dataclass(frozen=True)
class MetricContract:
    label: str
    measurement: str
    metric_type: str
    field: str
    accepted_types: frozenset[str]


CONTRACTS = (
    MetricContract(
        label="CPU",
        measurement="cpu",
        metric_type="cpu",
        field="cpu.usage_active",
        accepted_types=frozenset({"float", "double", "half_float", "scaled_float"}),
    ),
    MetricContract(
        label="memory",
        measurement="mem",
        metric_type="memory",
        field="mem.used_percent",
        accepted_types=frozenset({"float", "double", "half_float", "scaled_float"}),
    ),
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


def wait_for_opensearch() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            request_json("GET", "/_cluster/health")
            print("OpenSearch is available.", flush=True)
            return
        except ValidationError:
            print("Waiting for OpenSearch...", flush=True)
            time.sleep(3)
    raise ValidationError(f"OpenSearch was not available within {WAIT_SECONDS}s")


def field_caps(field_names: list[str]) -> dict[str, Any]:
    encoded_fields = urllib.parse.quote(",".join(field_names), safe=".,@*")
    response = request_json(
        "GET", f"/metrics-*/_field_caps?fields={encoded_fields}&include_unmapped=true"
    )
    return response.get("fields", {})


def require_field(
    capabilities: dict[str, Any],
    field_name: str,
    accepted_types: frozenset[str],
    *,
    searchable: bool = True,
    aggregatable: bool = True,
) -> None:
    definitions = capabilities.get(field_name)
    if not definitions:
        raise ValidationError(f"Required field {field_name!r} is missing from metrics-*")

    matching_types = accepted_types.intersection(definitions.keys())
    if not matching_types:
        actual_types = ", ".join(sorted(definitions.keys()))
        raise ValidationError(
            f"Field {field_name!r} has incompatible type(s): {actual_types}; "
            f"expected one of {sorted(accepted_types)}"
        )

    for field_type in matching_types:
        definition = definitions[field_type]
        if searchable and not definition.get("searchable", False):
            raise ValidationError(f"Field {field_name!r} is not searchable")
        if aggregatable and not definition.get("aggregatable", False):
            raise ValidationError(f"Field {field_name!r} is not aggregatable")


def metric_query(contract: MetricContract) -> dict[str, Any]:
    return {
        "size": 1,
        "track_total_hits": True,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"measurement_name": contract.measurement}},
                    {"term": {"tag.metric_type": contract.metric_type}},
                    {"exists": {"field": contract.field}},
                    {"range": {"@timestamp": {"gte": LOOKBACK}}},
                ]
            }
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
        "aggs": {
            "hosts": {
                "terms": {
                    "field": "tag.host_id",
                    "size": max(20, len(EXPECTED_HOSTS)),
                },
                "aggs": {
                    "latest_sample": {"max": {"field": "@timestamp"}},
                    "average_value": {"avg": {"field": contract.field}},
                },
            }
        },
    }


def extract_total_hits(response: dict[str, Any]) -> int:
    total = response.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        return int(total.get("value", 0))
    return int(total)


def validate_sample_structure(contract: MetricContract, source: dict[str, Any]) -> None:
    if source.get("measurement_name") != contract.measurement:
        raise ValidationError(
            f"Latest {contract.label} document has measurement_name="
            f"{source.get('measurement_name')!r}"
        )

    tag = source.get("tag")
    if not isinstance(tag, dict):
        raise ValidationError(f"Latest {contract.label} document has no tag object")

    if tag.get("metric_type") != contract.metric_type:
        raise ValidationError(
            f"Latest {contract.label} document has tag.metric_type="
            f"{tag.get('metric_type')!r}"
        )

    current: Any = source
    for component in contract.field.split("."):
        if not isinstance(current, dict) or component not in current:
            raise ValidationError(
                f"Latest {contract.label} document does not contain {contract.field}"
            )
        current = current[component]

    if not isinstance(current, (int, float)) or isinstance(current, bool):
        raise ValidationError(
            f"Latest {contract.label} value {contract.field} is not numeric: {current!r}"
        )


def validate_contract(contract: MetricContract) -> bool:
    response = request_json("POST", "/metrics-*/_search", metric_query(contract))
    total = extract_total_hits(response)
    hits = response.get("hits", {}).get("hits", [])
    buckets = response.get("aggregations", {}).get("hosts", {}).get("buckets", [])
    observed_hosts = {bucket.get("key") for bucket in buckets}
    missing_hosts = sorted(set(EXPECTED_HOSTS) - observed_hosts)

    if total <= 0 or not hits:
        print(
            f"Waiting for recent {contract.label} documents containing {contract.field}...",
            flush=True,
        )
        return False

    if missing_hosts:
        print(
            f"Waiting for {contract.label} telemetry from: {', '.join(missing_hosts)}",
            flush=True,
        )
        return False

    source = hits[0].get("_source", {})
    validate_sample_structure(contract, source)

    print(
        f"{contract.label}: {total} recent documents; "
        f"hosts={','.join(sorted(observed_hosts))}; field={contract.field}",
        flush=True,
    )
    return True


def validate_mapping_contract() -> None:
    required_fields = [
        "@timestamp",
        "measurement_name",
        "tag.host_id",
        "tag.metric_type",
        *(contract.field for contract in CONTRACTS),
    ]
    capabilities = field_caps(required_fields)

    require_field(capabilities, "@timestamp", frozenset({"date", "date_nanos"}))
    require_field(capabilities, "measurement_name", frozenset({"keyword"}))
    require_field(capabilities, "tag.host_id", frozenset({"keyword"}))
    require_field(capabilities, "tag.metric_type", frozenset({"keyword"}))

    for contract in CONTRACTS:
        require_field(capabilities, contract.field, contract.accepted_types)

    print("Field capabilities match the detector schema.", flush=True)


def main() -> int:
    try:
        wait_for_opensearch()
        deadline = time.monotonic() + WAIT_SECONDS

        while time.monotonic() < deadline:
            try:
                validate_mapping_contract()
                if all(validate_contract(contract) for contract in CONTRACTS):
                    print(
                        "Telemetry schema validation completed successfully: "
                        "native Telegraf documents, mappings, and five-host coverage match.",
                        flush=True,
                    )
                    return 0
            except ValidationError as exc:
                print(f"Telemetry is not ready: {exc}", flush=True)

            time.sleep(5)

        raise ValidationError(
            f"Telemetry did not satisfy the schema contract within {WAIT_SECONDS}s"
        )
    except (ValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
