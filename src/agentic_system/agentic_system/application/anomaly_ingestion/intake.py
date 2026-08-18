from __future__ import annotations

import asyncio
import logging

from ...domain.anomalies import AnomalyObservation


LOGGER = logging.getLogger("agentic_system.application.anomaly_ingestion")


class AnomalyIntake:
    """Consume anomaly observations independently from the OpenSearch poller.

    This application-layer component is intentionally small for now: it records
    that an anomaly has entered the autonomous backend. Incident persistence and
    Technical Lead dispatch will be attached here in subsequent steps.
    """

    def __init__(self, queue: asyncio.Queue[AnomalyObservation]) -> None:
        self.queue = queue
        self.running = False
        self.processed_count = 0
        self.last_anomaly: AnomalyObservation | None = None
        self.last_error: str | None = None

    async def _process(self, observation: AnomalyObservation) -> None:
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
