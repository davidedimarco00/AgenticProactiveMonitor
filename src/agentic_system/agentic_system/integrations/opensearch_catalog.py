from __future__ import annotations

from typing import Any

import httpx


def _term_value(filter_query: Any, field: str) -> str | None:
    if not isinstance(filter_query, dict):
        return None
    term = filter_query.get("term")
    if not isinstance(term, dict):
        return None
    value: Any = term.get(field)
    if isinstance(value, dict):
        value = value.get("value")
    normalized = str(value or "").strip()
    return normalized or None


def _feature_context(detector: dict[str, Any]) -> tuple[str | None, str | None]:
    attributes = detector.get("feature_attributes") or []
    if not isinstance(attributes, list):
        return None, None

    for attribute in attributes:
        if not isinstance(attribute, dict) or attribute.get("feature_enabled") is False:
            continue
        feature_name = str(attribute.get("feature_name") or "").strip() or None
        aggregation_query = attribute.get("aggregation_query") or {}
        if not isinstance(aggregation_query, dict):
            return feature_name, None

        for aggregation in aggregation_query.values():
            if not isinstance(aggregation, dict):
                continue
            avg = aggregation.get("avg")
            if isinstance(avg, dict):
                field = str(avg.get("field") or "").strip()
                if field:
                    return feature_name, field
        return feature_name, None

    return None, None


class OpenSearchDetectorCatalog:
    """Read-only adapter exposing normalized metadata for one anomaly detector."""

    def __init__(self, opensearch_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.opensearch_url = opensearch_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def get_detector_context(self, detector_id: str) -> dict[str, Any]:
        detector_id = detector_id.strip()
        if not detector_id:
            raise ValueError("detector_id cannot be empty")

        url = (
            f"{self.opensearch_url}/_plugins/_anomaly_detection/"
            f"detectors/{detector_id}"
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise RuntimeError("OpenSearch detector response is not a JSON object")

        detector = payload.get("anomaly_detector")
        if not isinstance(detector, dict):
            detector = payload

        detector_type = detector.get("detector_type") or payload.get("detector_type")
        if detector_type and str(detector_type).upper() != "SINGLE_ENTITY":
            raise RuntimeError(
                f"Detector {detector_id} is {detector_type!r}; "
                "AgenticProactiveMonitor requires SINGLE_ENTITY detectors"
            )

        indices = detector.get("indices") or []
        if not isinstance(indices, list):
            indices = []

        feature_name, feature_field = _feature_context(detector)
        measurement_name = _term_value(detector.get("filter_query"), "measurement_name")

        return {
            "detector_id": detector_id,
            "detector_type": "SINGLE_ENTITY",
            "name": str(detector.get("name") or "unknown"),
            "description": str(detector.get("description") or ""),
            "indices": [str(index) for index in indices],
            "time_field": str(detector.get("time_field") or "").strip() or None,
            "measurement_name": measurement_name,
            "feature_name": feature_name,
            "feature_field": feature_field,
        }
