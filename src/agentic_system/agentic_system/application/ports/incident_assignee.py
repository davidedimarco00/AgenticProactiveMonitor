from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


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
    """Application-facing port for assigning and triaging persisted incidents."""

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
