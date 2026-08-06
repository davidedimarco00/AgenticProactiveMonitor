#!/usr/bin/env python3
"""Validate the complete native Telegraf telemetry contract.

The script does not create or transform telemetry. It waits for real samples and
verifies document nesting, tags, mappings, field capabilities, and five-host
coverage before Dashboards and anomaly detectors are provisioned.
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
class MeasurementContract:
    measurement: str
    metric_type: str
    detector_field: str | None = None
    accepted_types: frozenset[str] = frozenset()


FLOAT_TYPES = frozenset({"float", "double", "half_float", "scaled_float"})

MEASUREMENT_CONTRACTS = (
    MeasurementContract("cpu", "cpu", "cpu.usage_active", FLOAT_TYPES),
    MeasurementContract("mem", "memory", "mem.used_percent", FLOAT_TYPES),
    MeasurementContract("disk", "disk"),
    MeasurementContract("diskio", "diskio"),
    MeasurementContract("net", "network"),
    MeasurementContract("system", "system"),
    MeasurementContract("swap", "swap"),
    MeasurementContract("processes", "processes"),
    MeasurementContract("kernel", "kernel"),
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


def measurement_query(contract: MeasurementContract) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {"term": {"measurement_name": contract.measurement}},
        {"term": {"tag.metric_type": contract.metric_type}},
        {"range": {"@timestamp": {"gte": LOOKBACK}}},
    ]
    if contract.detector_field:
        filters.append({"exists": {"field": contract.detector_field}})

    aggregations: dict[str, Any] = {
        "hosts": {
            "terms": {
                "field": "tag.host_id",
                "size": max(20, len(EXPECTED_HOSTS)),
            },
            "aggs": {"latest_sample": {"max": {"field": "@timestamp"}}},
        }
    }
    if contract.detector_field:
        aggregations["hosts"]["aggs"]["average_value"] = {
            "avg": {"field": contract.detector_field}
        }

    return {
        "size": 1,
        "track_total_hits": True,
        "query": {"bool": {"filter": filters}},
        "sort": [{"@timestamp": {"order": "desc"}}],
        "aggs": aggregations,
    }


def extract_total_hits(response: dict[str, Any]) -> int:
    total = response.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        return int(total.get("value", 0))
    return int(total)


def nested_value(source: dict[str, Any], field_name: str) -> Any:
    current: Any = source
    for component in field_name.split("."):
        if not isinstance(current, dict) or component not in current:
            raise ValidationError(f"Document does not contain {field_name}")
        current = current[component]
    return current


def validate_sample_structure(
    contract: MeasurementContract, source: dict[str, Any]
) -> None:
    if source.get("measurement_name") != contract.measurement:
        raise ValidationError(
            f"Latest {contract.measurement} document has measurement_name="
            f"{source.get('measurement_name')!r}"
        )

    tag = source.get("tag")
    if not isinstance(tag, dict):
        raise ValidationError(
            f"Latest {contract.measurement} document has no tag object"
        )

    if tag.get("metric_type") != contract.metric_type:
        raise ValidationError(
            f"Latest {contract.measurement} document has tag.metric_type="
            f"{tag.get('metric_type')!r}; expected {contract.metric_type!r}"
        )

    host_id = tag.get("host_id")
    if host_id not in EXPECTED_HOSTS:
        raise ValidationError(
            f"Latest {contract.measurement} document has invalid tag.host_id={host_id!r}"
        )

    measurement_object = source.get(contract.measurement)
    if not isinstance(measurement_object, dict) or not measurement_object:
        raise ValidationError(
            f"Latest {contract.measurement} document does not contain a non-empty "
            f"{contract.measurement} object"
        )

    if contract.detector_field:
        value = nested_value(source, contract.detector_field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(
                f"Latest {contract.measurement} value {contract.detector_field} "
                f"is not numeric: {value!r}"
            )


def validate_measurement(contract: MeasurementContract) -> bool:
    response = request_json(
        "POST", "/metrics-*/_search", measurement_query(contract)
    )
    total = extract_total_hits(response)
    hits = response.get("hits", {}).get("hits", [])
    buckets = response.get("aggregations", {}).get("hosts", {}).get("buckets", [])
    observed_hosts = {bucket.get("key") for bucket in buckets}
    missing_hosts = sorted(set(EXPECTED_HOSTS) - observed_hosts)

    if total <= 0 or not hits:
        print(
            f"Waiting for recent {contract.measurement} documents...", flush=True
        )
        return False

    if missing_hosts:
        print(
            f"Waiting for {contract.measurement} telemetry from: "
            f"{', '.join(missing_hosts)}",
            flush=True,
        )
        return False

    source = hits[0].get("_source", {})
    validate_sample_structure(contract, source)

    field_description = (
        f"; detector_field={contract.detector_field}"
        if contract.detector_field
        else ""
    )
    print(
        f"{contract.measurement}: {total} recent documents; "
        f"hosts={','.join(sorted(observed_hosts))}{field_description}",
        flush=True,
    )
    return True


def validate_mapping_contract() -> None:
    required_fields = [
        "@timestamp",
        "measurement_name",
        "tag.host_id",
        "tag.metric_type",
        *(
            contract.detector_field
            for contract in MEASUREMENT_CONTRACTS
            if contract.detector_field
        ),
    ]
    capabilities = field_caps(required_fields)

    require_field(capabilities, "@timestamp", frozenset({"date", "date_nanos"}))
    require_field(capabilities, "measurement_name", frozenset({"keyword"}))
    require_field(capabilities, "tag.host_id", frozenset({"keyword"}))
    require_field(capabilities, "tag.metric_type", frozenset({"keyword"}))

    for contract in MEASUREMENT_CONTRACTS:
        if contract.detector_field:
            require_field(
                capabilities,
                contract.detector_field,
                contract.accepted_types,
            )

    print("Field capabilities match the detector schema.", flush=True)


def main() -> int:
    try:
        wait_for_opensearch()
        deadline = time.monotonic() + WAIT_SECONDS

        while time.monotonic() < deadline:
            try:
                validate_mapping_contract()
                results = [
                    validate_measurement(contract)
                    for contract in MEASUREMENT_CONTRACTS
                ]
                if all(results):
                    print(
                        "Telemetry schema validation completed successfully: all "
                        "configured Telegraf measurements use the native nested "
                        "document structure on all five machines.",
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
