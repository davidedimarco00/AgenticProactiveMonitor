from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .anomalies import AnomalyObservation


LOGGER = logging.getLogger("agentic_system.incidents.ingestion")
AnomalyHandler = Callable[[AnomalyObservation], Awaitable[object]]


class AnomalyIntake:
    """Consume anomaly observations independently from the OpenSearch poller.

    `submit()` adds an acknowledgement boundary around the in-memory queue. The
    OpenSearch watcher only marks a result as delivered after the complete
    downstream workflow succeeds. If persistence, LLM reasoning or another
    dependency fails, the exception is returned to the watcher and the same
    OpenSearch result remains eligible for a later poll.
    """

    def __init__(
        self,
        queue: asyncio.Queue[AnomalyObservation],
        *,
        on_anomaly: AnomalyHandler | None = None,
    ) -> None:
        self.queue = queue
        self.on_anomaly = on_anomaly
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.last_anomaly: AnomalyObservation | None = None
        self.last_error: str | None = None
        self._delivery_acks: dict[str, asyncio.Future[None]] = {}

    async def submit(self, observation: AnomalyObservation) -> None:
        """Queue one observation and wait until its downstream workflow commits."""

        loop = asyncio.get_running_loop()
        key = observation.deduplication_key
        existing = self._delivery_acks.get(key)
        if existing is not None:
            await asyncio.shield(existing)
            return

        completed: asyncio.Future[None] = loop.create_future()
        self._delivery_acks[key] = completed
        try:
            await self.queue.put(observation)
            await asyncio.shield(completed)
        finally:
            if self._delivery_acks.get(key) is completed:
                self._delivery_acks.pop(key, None)

    async def _process(self, observation: AnomalyObservation) -> None:
        if self.on_anomaly is not None:
            await self.on_anomaly(observation)

        self.processed_count += 1
        self.last_anomaly = observation
        self.last_error = None
        LOGGER.warning(
            "OpenSearch anomaly entered agentic intake: result=%s detector=%s grade=%.3f confidence=%.3f",
            observation.result_id,
            observation.detector_id,
            observation.anomaly_grade,
            observation.confidence,
        )

    async def run(self) -> None:
        self.running = True
        LOGGER.info("Anomaly intake worker started")
        try:
            while True:
                observation = await self.queue.get()
                key = observation.deduplication_key
                acknowledgement = self._delivery_acks.get(key)
                try:
                    await self._process(observation)
                except asyncio.CancelledError:
                    if acknowledgement is not None and not acknowledgement.done():
                        acknowledgement.cancel()
                    raise
                except Exception as exc:
                    self.failed_count += 1
                    self.last_error = str(exc)
                    if acknowledgement is not None and not acknowledgement.done():
                        acknowledgement.set_exception(exc)
                    LOGGER.exception(
                        "Anomaly intake processing failed; result remains retryable: %s",
                        exc,
                    )
                else:
                    if acknowledgement is not None and not acknowledgement.done():
                        acknowledgement.set_result(None)
                finally:
                    self.queue.task_done()
        finally:
            self.running = False
            LOGGER.info("Anomaly intake worker stopped")
