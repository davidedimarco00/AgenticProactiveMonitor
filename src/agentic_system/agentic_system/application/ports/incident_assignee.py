from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class IncidentAssigneeReceipt:
    """Confirmation that an agent accepted an incident into its local workflow."""

    incident_id: str
    agent_role: str
    agent_jid: str


class IncidentAssigneePort(Protocol):
    """Application-facing port for assigning a persisted incident to an agent."""

    async def assign_incident(
        self,
        incident: dict[str, Any],
    ) -> IncidentAssigneeReceipt: ...
