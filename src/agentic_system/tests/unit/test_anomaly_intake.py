from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from agentic_system.incidents import AnomalyIntake, AnomalyObservation


def _observation(result_id: str) -> AnomalyObservation:
    return AnomalyObservation(
        result_id=result_id,
        result_index=".opensearch-anomaly-results-history-test",
        detector_id="detector-123",
        detector_name="CPU-processing-service",
        anomaly_grade=1.0,
        confidence=0.91,
        anomaly_score=6.4,
        data_start_time=1_700_000_000_000,
        data_end_time=1_700_000_060_000,
        execution_start_time=1_700_000_120_000,
        execution_end_time=1_700_000_121_000,
    )


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    async def wait_loop() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait_loop(), timeout=timeout)


class FakeAnomalyInbox:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.trace: list[str] = []

    async def record_anomaly(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = f"{payload['result_index']}:{payload['result_id']}"
        self.trace.append("record")
        return self.records.setdefault(
            key,
            {
                **payload,
                "anomaly_key": key,
                "state": "WAITING",
                "incident_id": None,
            },
        )

    async def get_anomaly(self, anomaly_key: str) -> dict[str, Any] | None:
        return self.records.get(anomaly_key)

    async def update_detector_metadata(
        self,
        anomaly_key: str,
        detector_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        record = self.records.get(anomaly_key)
        if record is not None:
            record["detector_name"] = detector_context.get("name")
        return record

    async def mark_anomaly_processing(self, anomaly_key: str) -> dict[str, Any] | None:
        self.trace.append("processing")
        record = self.records.get(anomaly_key)
        if record is None:
            return None
        if record.get("state") == "DISMISSED":
            return None
        if record.get("state") not in {"WAITING", "PROCESSING"}:
            return None
        record["state"] = "PROCESSING"
        return record

    async def mark_anomaly_completed(self, anomaly_key: str) -> dict[str, Any] | None:
        self.trace.append("completed")
        record = self.records.get(anomaly_key)
        if record is not None and record.get("state") != "DISMISSED":
            record["state"] = "COMPLETED"
            return record
        return None

    async def mark_anomaly_retryable(
        self,
        anomaly_key: str,
        *,
        error: str,
    ) -> dict[str, Any] | None:
        self.trace.append("retryable")
        record = self.records.get(anomaly_key)
        if record is not None and record.get("state") != "DISMISSED":
            record["state"] = "WAITING"
            record["last_error"] = error
            return record
        return None

    async def dismiss_waiting_anomaly(
        self,
        anomaly_key: str,
        *,
        dismissed_by: str = "operator",
        reason: str = "Marked as not a true anomaly by the operator.",
    ) -> dict[str, Any] | None:
        record = self.records.get(anomaly_key)
        if record is None or record.get("state") != "WAITING" or record.get("incident_id"):
            return None
        record["state"] = "DISMISSED"
        record["dismissed_by"] = dismissed_by
        record["dismissal_reason"] = reason
        return record

    async def link_anomaly_to_incident(
        self,
        anomaly_key: str,
        incident_id: str,
    ) -> dict[str, Any] | None:
        record = self.records.get(anomaly_key)
        if record is not None:
            record["incident_id"] = incident_id
        return record

    async def mark_incident_anomalies_processing(self, incident_id: str) -> int:
        return 0

    async def mark_incident_anomalies_completed(self, incident_id: str) -> int:
        return 0


def test_global_fifo_allows_only_one_active_anomaly_until_workflow_finishes() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=8)
        gates = {
            "result-1": asyncio.Event(),
            "result-2": asyncio.Event(),
            "result-3": asyncio.Event(),
        }
        trace: list[str] = []
        active = 0
        max_active = 0

        async def handler(observation: AnomalyObservation) -> object:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            trace.append(f"start:{observation.result_id}")
            await gates[observation.result_id].wait()
            trace.append(f"end:{observation.result_id}")
            active -= 1
            return {"incident_id": observation.result_id}

        intake = AnomalyIntake(queue, on_anomaly=handler)
        assert await intake.enqueue(_observation("result-1")) is True
        assert await intake.enqueue(_observation("result-2")) is True
        assert await intake.enqueue(_observation("result-3")) is True

        worker = asyncio.create_task(intake.run())
        await _wait_until(lambda: trace == ["start:result-1"])
        assert intake.active_anomaly is not None
        assert intake.active_anomaly.result_id == "result-1"
        assert queue.qsize() == 2
        assert max_active == 1

        gates["result-1"].set()
        await _wait_until(lambda: "start:result-2" in trace)
        assert trace[:3] == ["start:result-1", "end:result-1", "start:result-2"]
        assert max_active == 1

        gates["result-2"].set()
        await _wait_until(lambda: "start:result-3" in trace)
        assert max_active == 1

        gates["result-3"].set()
        await asyncio.wait_for(queue.join(), timeout=1)

        assert trace == [
            "start:result-1",
            "end:result-1",
            "start:result-2",
            "end:result-2",
            "start:result-3",
            "end:result-3",
        ]
        assert intake.processed_count == 3
        assert intake.active_anomaly is None
        assert max_active == 1

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    asyncio.run(scenario())


def test_duplicate_result_is_not_queued_while_owned_or_after_completion() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=4)
        release = asyncio.Event()

        async def handler(observation: AnomalyObservation) -> object:
            await release.wait()
            return {"ok": observation.result_id}

        intake = AnomalyIntake(queue, on_anomaly=handler)
        observation = _observation("result-duplicate")
        assert await intake.enqueue(observation) is True
        assert await intake.enqueue(observation) is False

        worker = asyncio.create_task(intake.run())
        await _wait_until(lambda: intake.active_anomaly is not None)
        assert await intake.enqueue(observation) is False

        release.set()
        await asyncio.wait_for(queue.join(), timeout=1)
        assert await intake.enqueue(observation) is False
        assert intake.duplicate_count == 3

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    asyncio.run(scenario())


def test_failed_anomaly_releases_ownership_and_does_not_block_next_queue_item() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=4)
        attempts: dict[str, int] = {}
        completed: list[str] = []

        async def flaky_handler(observation: AnomalyObservation) -> object:
            attempts[observation.result_id] = attempts.get(observation.result_id, 0) + 1
            if observation.result_id == "result-failing" and attempts[observation.result_id] == 1:
                raise RuntimeError("temporary mongodb outage")
            completed.append(observation.result_id)
            return {"incident_id": observation.result_id}

        intake = AnomalyIntake(queue, on_anomaly=flaky_handler)
        worker = asyncio.create_task(intake.run())

        failing = _observation("result-failing")
        healthy = _observation("result-healthy")
        assert await intake.enqueue(failing) is True
        assert await intake.enqueue(healthy) is True
        await asyncio.wait_for(queue.join(), timeout=1)

        assert intake.running is True
        assert intake.failed_count == 1
        assert intake.processed_count == 1
        assert completed == ["result-healthy"]

        # The failed result is no longer owned, so a later OpenSearch poll can
        # enqueue it again without restarting the agent team.
        assert await intake.enqueue(failing) is True
        await asyncio.wait_for(queue.join(), timeout=1)
        assert completed == ["result-healthy", "result-failing"]
        assert attempts["result-failing"] == 2
        assert intake.processed_count == 2
        assert intake.last_error is None

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    asyncio.run(scenario())


def test_fresh_anomaly_is_persisted_before_processing_and_completed_durably() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=4)
        inbox = FakeAnomalyInbox()

        async def handler(observation: AnomalyObservation) -> object:
            inbox.trace.append("handler")
            return {"incident_id": observation.result_id}

        intake = AnomalyIntake(queue, on_anomaly=handler, anomaly_inbox=inbox)
        observation = _observation("result-durable")
        assert await intake.enqueue(observation) is True
        assert inbox.trace == ["record"]
        assert inbox.records[observation.deduplication_key]["state"] == "WAITING"

        worker = asyncio.create_task(intake.run())
        await asyncio.wait_for(queue.join(), timeout=1)

        assert inbox.trace == ["record", "processing", "handler", "completed"]
        assert inbox.records[observation.deduplication_key]["state"] == "COMPLETED"

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    asyncio.run(scenario())


def test_failed_durable_anomaly_returns_to_waiting_for_retry() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=4)
        inbox = FakeAnomalyInbox()

        async def handler(_observation: AnomalyObservation) -> object:
            inbox.trace.append("handler")
            raise RuntimeError("temporary dependency failure")

        intake = AnomalyIntake(queue, on_anomaly=handler, anomaly_inbox=inbox)
        observation = _observation("result-retryable")
        assert await intake.enqueue(observation) is True

        worker = asyncio.create_task(intake.run())
        await asyncio.wait_for(queue.join(), timeout=1)

        record = inbox.records[observation.deduplication_key]
        assert record["state"] == "WAITING"
        assert record["last_error"] == "temporary dependency failure"
        assert inbox.trace == ["record", "processing", "handler", "retryable"]
        assert intake.failed_count == 1

        # A later watcher poll sees WAITING, so the same durable work can retry.
        assert await intake.enqueue(observation) is True

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    asyncio.run(scenario())


def test_operator_dismissed_waiting_anomaly_is_skipped_before_agentic_handler() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=4)
        inbox = FakeAnomalyInbox()
        handled: list[str] = []

        async def handler(observation: AnomalyObservation) -> object:
            handled.append(observation.result_id)
            return {"incident_id": observation.result_id}

        intake = AnomalyIntake(queue, on_anomaly=handler, anomaly_inbox=inbox)
        observation = _observation("result-false-positive")
        assert await intake.enqueue(observation) is True

        dismissed = await inbox.dismiss_waiting_anomaly(observation.deduplication_key)
        assert dismissed is not None
        assert dismissed["state"] == "DISMISSED"

        worker = asyncio.create_task(intake.run())
        await asyncio.wait_for(queue.join(), timeout=1)

        assert handled == []
        assert intake.processed_count == 0
        assert intake.failed_count == 0
        assert intake.dismissed_count == 1
        assert inbox.records[observation.deduplication_key]["state"] == "DISMISSED"

        # A later OpenSearch poll cannot resurrect the dismissed false positive.
        assert await intake.enqueue(observation) is False

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    asyncio.run(scenario())
