from __future__ import annotations

import asyncio
from collections import deque
import logging
from typing import Awaitable, Callable

from .anomalies import AnomalyObservation


LOGGER = logging.getLogger("agentic_system.incidents.ingestion")
AnomalyHandler = Callable[[AnomalyObservation], Awaitable[object]]


class AnomalyIntake:
    """Global FIFO gate for the complete multi-agent anomaly workflow.

    The queue has exactly one consumer. Therefore only one anomaly can be owned
    by the agentic team at a time. Additional anomalies remain queued until the
    current `on_anomaly` workflow returns. The handler boundary is intentionally
    the whole collaborative workflow: Technical Lead takeover/BDI, specialist
    work, synthesis, remediation and validation must all complete before the
    next queued anomaly is started.

    Successful observations are remembered for the current runtime so repeated
    OpenSearch polls do not enqueue duplicates. A failed observation is released
    from that deduplication set and can be queued again on a later poll.
    """

    def __init__(
        self,
        queue: asyncio.Queue[AnomalyObservation],
        *,
        on_anomaly: AnomalyHandler | None = None,
        deduplication_capacity: int = 4096,
    ) -> None:
        if deduplication_capacity <= 0:
            raise ValueError("deduplication_capacity must be greater than zero")

        self.queue = queue
        self.on_anomaly = on_anomaly
        self.deduplication_capacity = deduplication_capacity
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.enqueued_count = 0
        self.duplicate_count = 0
        self.last_anomaly: AnomalyObservation | None = None
        self.active_anomaly: AnomalyObservation | None = None
        self.last_error: str | None = None
        self._owned_keys: set[str] = set()
        self._completed_keys: set[str] = set()
        self._completed_order: deque[str] = deque()

    async def enqueue(self, observation: AnomalyObservation) -> bool:
        """Add one anomaly to the global FIFO queue if it is not already owned.

        Returns immediately after the item is queued. `False` means the same
        OpenSearch result is already queued, active or successfully completed in
        this runtime.
        """

        key = observation.deduplication_key
        if key in self._owned_keys or key in self._completed_keys:
            self.duplicate_count += 1
            return False

        # Claim before awaiting queue capacity so a repeated watcher poll cannot
        # insert the same anomaly twice while backpressure is active.
        self._owned_keys.add(key)
        try:
            await self.queue.put(observation)
        except BaseException:
            self._owned_keys.discard(key)
            raise

        self.enqueued_count += 1
        LOGGER.info(
            "Anomaly queued for exclusive agentic processing: result=%s detector=%s depth=%d",
            observation.result_id,
            observation.detector_id,
            self.queue.qsize(),
        )
        return True

    async def submit(self, observation: AnomalyObservation) -> bool:
        """Backward-compatible alias for queue submission."""

        return await self.enqueue(observation)

    def _remember_completed(self, key: str) -> None:
        if key in self._completed_keys:
            return
        if len(self._completed_order) >= self.deduplication_capacity:
            oldest = self._completed_order.popleft()
            self._completed_keys.discard(oldest)
        self._completed_order.append(key)
        self._completed_keys.add(key)

    async def _process(self, observation: AnomalyObservation) -> None:
        if self.on_anomaly is not None:
            # This await is the exclusivity boundary. The next FIFO item cannot
            # start until the complete collaborative workflow returns.
            await self.on_anomaly(observation)

        self.processed_count += 1
        self.last_anomaly = observation
        self.last_error = None
        LOGGER.warning(
            "Agentic team completed anomaly workflow: result=%s detector=%s grade=%.3f confidence=%.3f",
            observation.result_id,
            observation.detector_id,
            observation.anomaly_grade,
            observation.confidence,
        )

    async def run(self) -> None:
        self.running = True
        LOGGER.info("Global anomaly FIFO worker started with concurrency=1")
        try:
            while True:
                observation = await self.queue.get()
                key = observation.deduplication_key
                self.active_anomaly = observation
                try:
                    await self._process(observation)
                except asyncio.CancelledError:
                    # The item remains recoverable from OpenSearch after restart.
                    self._owned_keys.discard(key)
                    raise
                except Exception as exc:
                    self.failed_count += 1
                    self.last_error = str(exc)
                    # Failure belongs to this work item, not to the worker. Do
                    # not poison the queue; release the result so a later poll
                    # can retry it after dependencies recover.
                    self._owned_keys.discard(key)
                    LOGGER.exception(
                        "Exclusive anomaly workflow failed; result will be retryable: %s",
                        exc,
                    )
                else:
                    self._owned_keys.discard(key)
                    self._remember_completed(key)
                finally:
                    self.active_anomaly = None
                    self.queue.task_done()
        finally:
            self.active_anomaly = None
            self.running = False
            LOGGER.info("Global anomaly FIFO worker stopped")
