from __future__ import annotations

import logging

from ...domain.anomalies import AnomalyObservation
from ..ports.incident_assignee import IncidentAssigneePort
from .models import IncidentWorkflowResult
from .workflow import IncidentWorkflow


LOGGER = logging.getLogger("agentic_system.application.incidents.coordinator")


class IncidentCoordinator:
    """Orchestrate persistence and first assignment without embedding agent logic."""

    def __init__(
        self,
        workflow: IncidentWorkflow,
        assignee: IncidentAssigneePort,
    ) -> None:
        self.workflow = workflow
        self.assignee = assignee
        self.assigned_count = 0
        self.last_assigned_incident_id: str | None = None

    async def handle_anomaly(
        self,
        observation: AnomalyObservation,
    ) -> IncidentWorkflowResult:
        result = await self.workflow.handle_anomaly(observation)

        # Re-observations remain attached to the already active incident and do
        # not enqueue a duplicate assignment to the Technical Lead.
        if not result.created:
            return result

        receipt = await self.assignee.assign_incident(result.incident)
        updated = await self.workflow.mark_taken_in_charge(
            receipt.incident_id,
            agent_role=receipt.agent_role,
            agent_jid=receipt.agent_jid,
        )
        self.assigned_count += 1
        self.last_assigned_incident_id = receipt.incident_id
        LOGGER.info(
            "Assigned incident=%s to %s [%s]",
            receipt.incident_id,
            receipt.agent_role,
            receipt.agent_jid,
        )
        return IncidentWorkflowResult(
            incident=updated,
            created=True,
            correlated=False,
        )
