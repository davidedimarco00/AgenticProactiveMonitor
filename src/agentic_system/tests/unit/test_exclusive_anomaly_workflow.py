from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from agentic_system.incidents import AnomalyObservation, IncidentCoordinator
from agentic_system.incidents.models import IncidentWorkflowResult


class WaitingRepository:
    def __init__(self) -> None:
        self.incident: dict[str, Any] = {
            "incident_id": "INC-EXCLUSIVE-001",
            "status": "TRIAGED",
            "agentic": {"investigation_task_id": "TASK-EXCLUSIVE-001"},
        }
        self.task: dict[str, Any] = {
            "task_id": "TASK-EXCLUSIVE-001",
            "incident_id": "INC-EXCLUSIVE-001",
            "state": "RUNNING",
        }

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        assert incident_id == "INC-EXCLUSIVE-001"
        return deepcopy(self.incident)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        assert task_id == "TASK-EXCLUSIVE-001"
        return deepcopy(self.task)


class WorkflowStub:
    async def mark_operator_action_required(
        self,
        incident_id: str,
        *,
        task_id: str,
        reason: str,
    ) -> dict[str, Any]:
        raise AssertionError(
            f"Unexpected operator escalation for {incident_id}/{task_id}: {reason}"
        )


def _observation() -> AnomalyObservation:
    return AnomalyObservation(
        result_id="result-exclusive-1",
        result_index=".opendistro-anomaly-results-history-test",
        detector_id="detector-exclusive-1",
        anomaly_grade=1.0,
        confidence=0.95,
        anomaly_score=5.0,
        data_start_time=None,
        data_end_time=None,
        execution_start_time=None,
        execution_end_time=None,
    )


def test_exclusive_handler_does_not_release_running_specialist_task() -> None:
    async def scenario() -> None:
        repository = WaitingRepository()
        coordinator = IncidentCoordinator(
            WorkflowStub(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            repository,  # type: ignore[arg-type]
        )

        async def already_triaged(_observation: AnomalyObservation) -> IncidentWorkflowResult:
            return IncidentWorkflowResult(
                incident=deepcopy(repository.incident),
                created=True,
                correlated=False,
            )

        coordinator.handle_anomaly = already_triaged  # type: ignore[method-assign]

        processing = asyncio.create_task(coordinator.handle_anomaly_exclusively(_observation()))

        # RUNNING means the selected specialist owns the durable task. The
        # current anomaly must still occupy the single global FIFO slot.
        await asyncio.sleep(0.05)
        assert processing.done() is False

        repository.incident["status"] = "RESOLVED"
        result = await asyncio.wait_for(processing, timeout=1.0)
        assert result["status"] == "RESOLVED"

    asyncio.run(scenario())
