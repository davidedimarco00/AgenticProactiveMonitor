from __future__ import annotations

from typing import Any

import httpx


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

        return {
            "detector_id": detector_id,
            "detector_type": "SINGLE_ENTITY",
            "name": str(detector.get("name") or "unknown"),
            "description": str(detector.get("description") or ""),
            "indices": [str(index) for index in indices],
        }
