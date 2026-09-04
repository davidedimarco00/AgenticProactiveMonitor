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
