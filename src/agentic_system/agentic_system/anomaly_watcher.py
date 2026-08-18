from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
import logging
import time
from typing import Any, Awaitable, Callable

import httpx


LOGGER = logging.getLogger("agentic_system.anomaly_watcher")
ANOMALY_RESULTS_PATH = "/_plugins/_anomaly_detection/detectors/results/_search/"


@dataclass(frozen=True, slots=True)
class AnomalyObservation:
    """Normalized anomaly result delivered from OpenSearch to the agentic runtime.

    This is a transport/domain object, not a machine-learning model. It keeps only
    the anomaly metadata needed by the agentic workflow and intentionally excludes
    raw metric samples and raw logs.
    """

    result_id: str
    result_index: str
    detector_id: str
    anomaly_grade: float
    confidence: float
    anomaly_score: float | None
    data_start_time: int | None
    data_end_time: int | None
    execution_start_time: int | None
    execution_end_time: int | None

    @property
    def deduplication_key(self) -> str:
        return f"{self.result_index}:{self.result_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def anomaly_observation_from_hit(hit: dict[str, Any]) -> AnomalyObservation | None:
    """Convert one OpenSearch anomaly-result hit into the internal observation."""

    source = hit.get("_source")
    if not isinstance(source, dict):
        return None

    result_id = str(hit.get("_id") or "").strip()
    result_index = str(hit.get("_index") or "").strip()
    detector_id = str(source.get("detector_id") or "").strip()

    try:
        anomaly_grade = float(source.get("anomaly_grade") or 0.0)
        confidence = float(source.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None

    if not result_id or not result_index or not detector_id or anomaly_grade <= 0.0:
        return None

    try:
        return AnomalyObservation(
            result_id=result_id,
            result_index=result_index,
            detector_id=detector_id,
            anomaly_grade=anomaly_grade,
            confidence=confidence,
            anomaly_score=_optional_float(source.get("anomaly_score")),
            data_start_time=_optional_int(source.get("data_start_time")),
            data_end_time=_optional_int(source.get("data_end_time")),
            execution_start_time=_optional_int(source.get("execution_start_time")),
            execution_end_time=_optional_int(source.get("execution_end_time")),
        )
    except (TypeError, ValueError):
        return None


class OpenSearchAnomalyWatcher:
    """Poll OpenSearch anomaly results and deliver each new result exactly once per runtime.

    The watcher only observes and forwards anomalies. Incident persistence and agent
    dispatch are intentionally handled by later workflow stages.
    """

    def __init__(
        self,
        *,
        opensearch_url: str,
        on_anomaly: Callable[[AnomalyObservation], Awaitable[None]],
        poll_interval_seconds: float = 5.0,
        lookback_seconds: int = 300,
        request_timeout_seconds: float = 10.0,
        deduplication_capacity: int = 4096,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")
        if lookback_seconds <= 0:
            raise ValueError("lookback_seconds must be greater than zero")
        if deduplication_capacity <= 0:
            raise ValueError("deduplication_capacity must be greater than zero")

        self.opensearch_url = opensearch_url.rstrip("/")
        self.results_url = f"{self.opensearch_url}{ANOMALY_RESULTS_PATH}"
        self.on_anomaly = on_anomaly
        self.poll_interval_seconds = poll_interval_seconds
        self.lookback_seconds = lookback_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.deduplication_capacity = deduplication_capacity

        self.running = False
        self.poll_count = 0
        self.delivered_count = 0
        self.last_error: str | None = None
        self._stop_event = asyncio.Event()
        self._seen_keys: set[str] = set()
        self._seen_order: deque[str] = deque()

    def _search_body(self) -> dict[str, Any]:
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
            ],
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"anomaly_grade": {"gt": 0}}},
                        {"range": {"execution_end_time": {"gte": cutoff_ms}}},
                    ]
                }
            },
            "sort": [{"execution_end_time": {"order": "asc"}}],
        }

    async def _fetch_hits(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(self.results_url, json=self._search_body())
            response.raise_for_status()
            payload = response.json()

        hits = payload.get("hits", {}).get("hits", []) if isinstance(payload, dict) else []
        if not isinstance(hits, list):
            raise RuntimeError("OpenSearch anomaly result response has an invalid hits structure")
        return [hit for hit in hits if isinstance(hit, dict)]

    def _remember(self, key: str) -> None:
        if key in self._seen_keys:
            return

        if len(self._seen_order) >= self.deduplication_capacity:
            oldest = self._seen_order.popleft()
            self._seen_keys.discard(oldest)

        self._seen_order.append(key)
        self._seen_keys.add(key)

    async def poll_once(self) -> int:
        hits = await self._fetch_hits()
        self.poll_count += 1
        delivered = 0

        for hit in hits:
            observation = anomaly_observation_from_hit(hit)
            if observation is None:
                continue

            key = observation.deduplication_key
            if key in self._seen_keys:
                continue

            # Mark the result as seen only after the downstream callback succeeds.
            # If the next workflow stage fails, the anomaly is retried on a later poll.
            await self.on_anomaly(observation)
            self._remember(key)
            delivered += 1
            self.delivered_count += 1

        self.last_error = None
        return delivered

    async def run(self) -> None:
        self.running = True
        LOGGER.info(
            "OpenSearch anomaly watcher started: endpoint=%s poll=%.1fs lookback=%ss",
            self.results_url,
            self.poll_interval_seconds,
            self.lookback_seconds,
        )

        try:
            while not self._stop_event.is_set():
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = str(exc)
                    LOGGER.warning("OpenSearch anomaly watcher poll failed: %s", exc)

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            self.running = False
            LOGGER.info("OpenSearch anomaly watcher stopped")

    def stop(self) -> None:
        self._stop_event.set()
