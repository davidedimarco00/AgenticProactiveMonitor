from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class DetectorContextPort(Protocol):
    """Read-only contract for normalized OpenSearch detector metadata."""

    async def get_detector_context(self, detector_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class IncidentAssigneeReceipt:
    """Confirmation that an agent accepted an incident into its local workflow."""

    incident_id: str
    agent_role: str
    agent_jid: str


@dataclass(frozen=True, slots=True)
class IncidentTriageReceipt:
    """Technical Lead triage outcome after real BDI deliberation."""

    incident_id: str
    probable_domain: str
    primary_investigator: str
    confidence: float
    rationale: str
    bdi_goal: str
    bdi_triage_intention: str
    bdi_intention: str


class IncidentAssigneePort(Protocol):
    """Contract for assigning and triaging persisted incidents."""

    async def assign_incident(
        self,
        incident: dict[str, Any],
    ) -> IncidentAssigneeReceipt: ...

    async def triage_incident(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
    ) -> IncidentTriageReceipt: ...


class IncidentRepositoryPort(Protocol):
    """Persistence contract required by the autonomous incident workflow."""

    async def find_active_incident_by_detector(
        self,
        detector_id: str,
    ) -> dict[str, Any] | None:
        ...

    async def list_incidents(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
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

    # The coordinator must observe the durable task linked by an incident while
    # it keeps exclusive ownership of the global anomaly FIFO slot.
    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        ...


class AgentTaskRepositoryPort(Protocol):
    """Atomic persistence contract for durable agent work items."""

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        ...

    async def list_tasks(
        self,
        *,
        states: list[str] | None = None,
        incident_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        ...

    async def transition_task(
        self,
        task_id: str,
        *,
        expected_states: list[str],
        new_state: str,
        patch: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any] | None:
        ...
