from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from agentic_system.incidents import IncidentCoordinator


class RecoveryRepository:
    def __init__(self) -> None:
        self.incidents = [
            {
                "incident_id": "INC-NEWER",
                "status": "TRIAGED",
                "created_at": "2026-08-19T11:05:00+00:00",
                "anomaly": {
                    "detector_id": "NETLAT-api-gateway",
                    "grade": 1.0,
                    "confidence": 0.82,
                },
            },
            {
                "incident_id": "INC-OLDER",
                "status": "TRIAGED",
                "created_at": "2026-08-19T11:00:00+00:00",
                "anomaly": {
                    "detector_id": "CPU-processing-service",
                    "grade": 1.0,
                    "confidence": 0.91,
                },
            },
        ]

    async def list_incidents(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        del limit, query
        return deepcopy(
            [incident for incident in self.incidents if incident["status"] == status]
        )


def test_recovery_observations_are_seeded_oldest_first() -> None:
    repository = RecoveryRepository()
    coordinator = IncidentCoordinator(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
    )

    observations = asyncio.run(coordinator.build_recovery_observations())

    assert [item.recovery_incident_id for item in observations] == [
        "INC-OLDER",
        "INC-NEWER",
    ]
    assert [item.detector_id for item in observations] == [
        "CPU-processing-service",
        "NETLAT-api-gateway",
    ]
    assert all(item.result_index == "agentic-workflow-recovery" for item in observations)


class ReviewPendingRepository(RecoveryRepository):
    """An interrupted investigation next to one that already returned evidence."""

    def __init__(self) -> None:
        super().__init__()
        self.incidents = [
            {
                "incident_id": "INC-INVESTIGATING",
                "status": "UNDER_ANALYSIS",
                "created_at": "2026-08-19T11:00:00+00:00",
                "agentic": {"investigation_task_id": "TASK-RUNNING"},
                "anomaly": {"detector_id": "CPU-processing-service", "grade": 1.0, "confidence": 0.9},
            },
            {
                "incident_id": "INC-AWAITING-REVIEW",
                "status": "UNDER_ANALYSIS",
                "created_at": "2026-08-19T11:05:00+00:00",
                "agentic": {"investigation_task_id": "TASK-COMPLETED"},
                "anomaly": {"detector_id": "RAM-worker-service", "grade": 1.0, "confidence": 0.9},
            },
        ]
        self.tasks = {
            "TASK-RUNNING": {"task_id": "TASK-RUNNING", "state": "RETRYING"},
            "TASK-COMPLETED": {"task_id": "TASK-COMPLETED", "state": "COMPLETED"},
        }

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        return deepcopy(self.tasks.get(task_id))


def test_interrupted_investigation_is_recovered_but_a_pending_review_is_not() -> None:
    repository = ReviewPendingRepository()
    coordinator = IncidentCoordinator(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
    )

    observations = asyncio.run(coordinator.build_recovery_observations())

    # An investigation interrupted by a restart is re-admitted to the FIFO; one
    # whose task already completed cannot be driven forward by the workflow loop
    # and would hold the exclusive queue against every later anomaly.
    assert [item.recovery_incident_id for item in observations] == ["INC-INVESTIGATING"]
