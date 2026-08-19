from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..incidents import AnomalyObservation
from .opensearch_client import OpenSearchAnomalyClient
from .opensearch_mapper import anomaly_observation_from_hit


LOGGER = logging.getLogger("agentic_system.integrations.opensearch_watcher")
QueueAnomaly = Callable[[AnomalyObservation], Awaitable[bool]]


class OpenSearchAnomalyWatcher:
    """Poll OpenSearch and feed the global anomaly queue.

    Deduplication belongs to the queue because only that component knows whether
    an observation is queued, currently active, completed or released after a
    failure. The watcher therefore remains a producer and never starts parallel
    incident workflows by itself.
    """

    def __init__(
        self,
        *,
        opensearch_url: str,
        on_anomaly: QueueAnomaly,
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
        self.on_anomaly = on_anomaly
        self.poll_interval_seconds = poll_interval_seconds
        self.lookback_seconds = lookback_seconds
        self.request_timeout_seconds = request_timeout_seconds
        # Kept for configuration compatibility; deduplication is now owned by
        # the single-consumer anomaly queue.
        self.deduplication_capacity = deduplication_capacity
        self.client = OpenSearchAnomalyClient(
            opensearch_url=self.opensearch_url,
            lookback_seconds=lookback_seconds,
            request_timeout_seconds=request_timeout_seconds,
        )
        self.results_url = self.client.results_url

        self.running = False
        self.poll_count = 0
        self.delivered_count = 0
        self.duplicate_count = 0
        self.failed_delivery_count = 0
        self.last_error: str | None = None
        self._stop_event = asyncio.Event()

    async def _fetch_hits(self) -> list[dict[str, object]]:
        return await self.client.fetch_hits()

    async def poll_once(self) -> int:
        hits = await self._fetch_hits()
        self.poll_count += 1
        queued = 0
        delivery_error: str | None = None

        for hit in hits:
            observation = anomaly_observation_from_hit(hit)
            if observation is None:
                continue

            try:
                accepted = await self.on_anomaly(observation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failed_delivery_count += 1
                delivery_error = str(exc)
                LOGGER.warning(
                    "Could not enqueue anomaly result=%s detector=%s: %s",
                    observation.result_id,
                    observation.detector_id,
                    exc,
                )
                continue

            if not accepted:
                self.duplicate_count += 1
                continue

            queued += 1
            self.delivered_count += 1

        self.last_error = delivery_error
        return queued

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
                        self._stop_event.wait(), timeout=self.poll_interval_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            self.running = False
            LOGGER.info("OpenSearch anomaly watcher stopped")

    def stop(self) -> None:
        self._stop_event.set()
