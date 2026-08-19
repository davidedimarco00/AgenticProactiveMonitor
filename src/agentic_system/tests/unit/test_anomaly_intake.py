from __future__ import annotations

import asyncio
from contextlib import suppress

from agentic_system.incidents import AnomalyIntake, AnomalyObservation


def _observation(result_id: str) -> AnomalyObservation:
    return AnomalyObservation(
        result_id=result_id,
        result_index=".opensearch-anomaly-results-history-test",
        detector_id="detector-123",
        anomaly_grade=1.0,
        confidence=0.91,
        anomaly_score=6.4,
        data_start_time=1_700_000_000_000,
        data_end_time=1_700_000_060_000,
        execution_start_time=1_700_000_120_000,
        execution_end_time=1_700_000_121_000,
    )


def test_anomaly_intake_consumes_queue_independently_and_keeps_last_observation() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=4)
        intake = AnomalyIntake(queue)
        task = asyncio.create_task(intake.run())

        await queue.put(_observation("result-1"))
        await queue.put(_observation("result-2"))
        await asyncio.wait_for(queue.join(), timeout=1)

        assert intake.running is True
        assert intake.processed_count == 2
        assert intake.last_anomaly is not None
        assert intake.last_anomaly.result_id == "result-2"
        assert intake.last_error is None
        assert queue.empty()

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        assert intake.running is False

    asyncio.run(scenario())


def test_anomaly_intake_invokes_downstream_handler_before_marking_processed() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(maxsize=2)
        handled: list[str] = []

        async def handler(observation: AnomalyObservation) -> object:
            handled.append(observation.result_id)
            return {"ok": True}

        intake = AnomalyIntake(queue, on_anomaly=handler)
        task = asyncio.create_task(intake.run())

        await queue.put(_observation("result-handler"))
        await asyncio.wait_for(queue.join(), timeout=1)

        assert handled == ["result-handler"]
        assert intake.processed_count == 1
        assert intake.last_anomaly is not None
        assert intake.last_anomaly.result_id == "result-handler"

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
