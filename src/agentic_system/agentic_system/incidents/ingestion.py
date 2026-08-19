from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .anomalies import AnomalyObservation


LOGGER = logging.getLogger("agentic_system.incidents.ingestion")
AnomalyHandler = Callable[[AnomalyObservation], Awaitable[object]]


class AnomalyIntake:
    """Consume anomaly observations independently from the OpenSearch poller."""

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
        self.last_anomaly: AnomalyObservation | None = None
        self.last_error: str | None = None

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
                try:
                    await self._process(observation)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = str(exc)
                    LOGGER.exception("Anomaly intake processing failed: %s", exc)
                finally:
                    self.queue.task_done()
        finally:
            self.running = False
            LOGGER.info("Anomaly intake worker stopped")
