from __future__ import annotations

from typing import Any, Protocol


class IncidentRepositoryPort(Protocol):
    """Persistence contract required by the autonomous incident workflow."""

    async def find_active_incident_by_detector(
        self,
        detector_id: str,
    ) -> dict[str, Any] | None:
        ...

    async def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def update_incident(
        self,
        incident_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        ...

    async def add_event(
        self,
        incident_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        ...
