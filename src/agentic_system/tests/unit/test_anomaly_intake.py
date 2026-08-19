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


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    async def wait_loop() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait_loop(), timeout=timeout)


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
