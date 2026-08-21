from __future__ import annotations

import time
from typing import Any

import httpx


ANOMALY_RESULTS_PATH = "/_plugins/_anomaly_detection/detectors/results/_search/"


class OpenSearchAnomalyClient:
    """Async client restricted to real-time OpenSearch Anomaly Detection results.

    OpenSearch stores both real-time detector results and historical-analysis
    results in anomaly-result indexes. Real-time results are identified by the
    absence of ``task_id``; historical analysis results carry a task ID. The
    watcher therefore explicitly excludes documents that contain ``task_id``.
    """

    def __init__(
        self,
        *,
        opensearch_url: str,
        lookback_seconds: int,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self.opensearch_url = opensearch_url.rstrip("/")
        self.results_url = f"{self.opensearch_url}{ANOMALY_RESULTS_PATH}"
        self.lookback_seconds = lookback_seconds
        self.request_timeout_seconds = request_timeout_seconds

    def search_body(self) -> dict[str, Any]:
        cutoff_ms = int(time.time() * 1000) - (self.lookback_seconds * 1000)
        return {
            "size": 100,
            "_source": [
                "detector_id",
                "anomaly_grade",
                "confidence",
                "anomaly_score",
                "data_start_time",
                "data_end_time",
                "execution_start_time",
                "execution_end_time",
                "task_id",
            ],
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"anomaly_grade": {"gt": 0}}},
                        {"range": {"execution_end_time": {"gte": cutoff_ms}}},
                    ],
                    "must_not": [
                        {"exists": {"field": "task_id"}},
                    ],
                }
            },
            "sort": [{"execution_end_time": {"order": "asc"}}],
        }

    async def fetch_hits(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(self.results_url, json=self.search_body())
            response.raise_for_status()
            payload = response.json()

        hits = payload.get("hits", {}).get("hits", []) if isinstance(payload, dict) else []
        if not isinstance(hits, list):
            raise RuntimeError("OpenSearch anomaly result response has an invalid hits structure")
        return [hit for hit in hits if isinstance(hit, dict)]
