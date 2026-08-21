from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
import logging
from typing import Any, Awaitable, Callable

from .anomalies import AnomalyObservation
from .contracts import AnomalyInboxPort


LOGGER = logging.getLogger("agentic_system.incidents.ingestion")
AnomalyHandler = Callable[[AnomalyObservation], Awaitable[object]]
AnomalyMetadataResolver = Callable[[str], Awaitable[dict[str, Any]]]


class AnomalyDismissed(RuntimeError):
    """Internal signal used when an operator dismissed queued work before ownership."""


class AnomalyIntake:
    """Global FIFO gate backed by a durable MongoDB anomaly inbox.

    Fresh OpenSearch observations are persisted before any optional detector
    metadata lookup and before they are admitted to the in-memory FIFO. The queue
    has exactly one consumer, so the multi-agent team owns one anomaly at a time,
    while WAITING observations survive backend restarts. Synthetic incident-
    recovery observations deliberately bypass the anomaly inbox because they
    represent already-persisted incident ownership.
    """

    def __init__(
        self,
        queue: asyncio.Queue[AnomalyObservation],
        *,
        on_anomaly: AnomalyHandler | None = None,
        anomaly_inbox: AnomalyInboxPort | None = None,
        metadata_resolver: AnomalyMetadataResolver | None = None,
        deduplication_capacity: int = 4096,
    ) -> None:
        if deduplication_capacity <= 0:
            raise ValueError("deduplication_capacity must be greater than zero")

        self.queue = queue
        self.on_anomaly = on_anomaly
        self.anomaly_inbox = anomaly_inbox
        self.metadata_resolver = metadata_resolver
        self.deduplication_capacity = deduplication_capacity
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.dismissed_count = 0
        self.enqueued_count = 0
        self.duplicate_count = 0
        self.last_anomaly: AnomalyObservation | None = None
        self.active_anomaly: AnomalyObservation | None = None
        self.last_error: str | None = None
        self._owned_keys: set[str] = set()
        self._completed_keys: set[str] = set()
        self._completed_order: deque[str] = deque()
        self._detector_metadata_cache: dict[str, dict[str, Any]] = {}

    async def _enrich_detector_metadata(
        self,
        observation: AnomalyObservation,
    ) -> AnomalyObservation:
        """Best-effort enrichment after the raw anomaly has already been persisted."""

        if self.anomaly_inbox is None or self.metadata_resolver is None:
            return observation

        detector_id = observation.detector_id
        try:
            detector_context = self._detector_metadata_cache.get(detector_id)
            if detector_context is None:
                detector_context = await self.metadata_resolver(detector_id)
                self._detector_metadata_cache[detector_id] = dict(detector_context)

            persisted = await self.anomaly_inbox.update_detector_metadata(
                observation.deduplication_key,
                detector_context,
            )
            if persisted is None:
                return observation

            indices = detector_context.get("indices") or []
            if not isinstance(indices, list):
                indices = []
            return replace(
                observation,
                detector_name=str(detector_context.get("name") or "").strip() or None,
                detector_description=(
                    str(detector_context.get("description") or "").strip() or None
                ),
                detector_indices=tuple(str(index) for index in indices),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Readable labels are useful for operators but must never prevent an
            # already-persisted anomaly from entering the fault-tolerant FIFO.
            LOGGER.warning(
                "Could not enrich anomaly=%s with detector metadata: %s",
                observation.deduplication_key,
                exc,
            )
            return observation

    async def enqueue(self, observation: AnomalyObservation) -> bool:
        """Persist and queue one anomaly if it is not already durably owned."""

        key = observation.deduplication_key
        if key in self._owned_keys or key in self._completed_keys:
            self.duplicate_count += 1
            return False

        if self.anomaly_inbox is not None and not observation.recovery_incident_id:
            # Persistence is deliberately the first external step. If the backend
            # disappears during metadata enrichment, the raw anomaly is already
            # durable and will be recovered from MongoDB on the next startup.
            persisted = await self.anomaly_inbox.record_anomaly(observation.to_dict())
            persisted_state = str(persisted.get("state") or "WAITING").upper()

            # Terminal observations never replay. PROCESSING and RECOVERY are
            # already owned by this runtime/incident recovery. DISMISSED means
            # the operator explicitly classified this result as a false positive.
            if persisted_state in {"COMPLETED", "PROCESSING", "RECOVERY", "DISMISSED"}:
                self.duplicate_count += 1
                return False

            if persisted.get("detector_name"):
                observation = replace(
                    observation,
                    detector_name=str(persisted.get("detector_name") or "").strip() or None,
                    detector_description=(
                        str(persisted.get("detector_description") or "").strip() or None
                    ),
                    detector_indices=tuple(
                        str(index) for index in (persisted.get("detector_indices") or [])
                    ),
                )
            else:
                observation = await self._enrich_detector_metadata(observation)

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
            observation.detector_name or observation.detector_id,
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

    async def _mark_retryable(self, observation: AnomalyObservation, error: str) -> None:
        if self.anomaly_inbox is None or observation.recovery_incident_id:
            return
        try:
            await self.anomaly_inbox.mark_anomaly_retryable(
                observation.deduplication_key,
                error=error,
            )
        except Exception:
            LOGGER.exception(
                "Could not return anomaly=%s to durable WAITING state",
                observation.deduplication_key,
            )

    async def _process(self, observation: AnomalyObservation) -> None:
        if self.anomaly_inbox is not None and not observation.recovery_incident_id:
            persisted = await self.anomaly_inbox.mark_anomaly_processing(
                observation.deduplication_key
            )
            if persisted is None:
                current = await self.anomaly_inbox.get_anomaly(observation.deduplication_key)
                if current is not None and str(current.get("state") or "").upper() == "DISMISSED":
                    raise AnomalyDismissed(observation.deduplication_key)
                raise RuntimeError(
                    "Durable anomaly disappeared before processing: "
                    f"{observation.deduplication_key}"
                )

        if self.on_anomaly is not None:
            # This await is the exclusivity boundary. The next FIFO item cannot
            # start until the complete collaborative workflow returns.
            await self.on_anomaly(observation)

        if self.anomaly_inbox is not None and not observation.recovery_incident_id:
            completed = await self.anomaly_inbox.mark_anomaly_completed(
                observation.deduplication_key
            )
            if completed is None:
                current = await self.anomaly_inbox.get_anomaly(observation.deduplication_key)
                if current is not None and str(current.get("state") or "").upper() == "DISMISSED":
                    raise AnomalyDismissed(observation.deduplication_key)
                raise RuntimeError(
                    "Durable anomaly disappeared before completion: "
                    f"{observation.deduplication_key}"
                )

        self.processed_count += 1
        self.last_anomaly = observation
        self.last_error = None
        LOGGER.warning(
            "Agentic team completed anomaly workflow: result=%s detector=%s grade=%.3f confidence=%.3f",
            observation.result_id,
            observation.detector_name or observation.detector_id,
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
                except AnomalyDismissed:
                    self.dismissed_count += 1
                    self.last_error = None
                    self._owned_keys.discard(key)
                    self._remember_completed(key)
                    LOGGER.info(
                        "Skipped operator-dismissed anomaly before agentic ownership: %s",
                        key,
                    )
                except asyncio.CancelledError:
                    # Persisted fresh observations return to WAITING so they can
                    # be seeded again after restart. Synthetic incident recovery
                    # remains recoverable through the incident repository.
                    await self._mark_retryable(observation, "backend shutdown during processing")
                    self._owned_keys.discard(key)
                    raise
                except Exception as exc:
                    self.failed_count += 1
                    self.last_error = str(exc)
                    await self._mark_retryable(observation, str(exc))
                    # Failure belongs to this work item, not to the worker. Do
                    # not poison the queue; release the result so a later poll or
                    # backend recovery can retry it after dependencies recover.
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
