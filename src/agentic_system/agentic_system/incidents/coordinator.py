from __future__ import annotations

import logging

from .anomalies import AnomalyObservation
from .contracts import DetectorContextPort, IncidentAssigneePort
from .models import IncidentWorkflowResult
from .workflow import IncidentWorkflow


LOGGER = logging.getLogger("agentic_system.incidents.coordinator")


class IncidentCoordinator:
    """Orchestrate persistence, Technical Lead takeover and first BDI triage."""

    def __init__(
        self,
        workflow: IncidentWorkflow,
        assignee: IncidentAssigneePort,
        detector_context: DetectorContextPort,
    ) -> None:
        self.workflow = workflow
        self.assignee = assignee
        self.detector_context = detector_context
        self.assigned_count = 0
        self.triaged_count = 0
        self.last_assigned_incident_id: str | None = None
        self.last_triaged_incident_id: str | None = None

    async def handle_anomaly(
        self,
        observation: AnomalyObservation,
    ) -> IncidentWorkflowResult:
        try:
            detector_context = await self.detector_context.get_detector_context(
                observation.detector_id
            )
        except Exception as exc:
            LOGGER.warning(
                "Could not resolve OpenSearch detector metadata for %s: %s",
                observation.detector_id,
                exc,
            )
            detector_context = {
                "detector_id": observation.detector_id,
                "detector_type": "SINGLE_ENTITY",
                "name": "unknown",
                "description": "",
                "indices": [],
            }

        result = await self.workflow.handle_anomaly(
            observation,
            detector_context=detector_context,
        )

        # Re-observations remain attached to the already active incident and do
        # not enqueue duplicate takeover or triage work.
        if not result.created:
            return result

        assignment_receipt = await self.assignee.assign_incident(result.incident)
        taken_in_charge = await self.workflow.mark_taken_in_charge(
            assignment_receipt.incident_id,
            agent_role=assignment_receipt.agent_role,
            agent_jid=assignment_receipt.agent_jid,
        )
        self.assigned_count += 1
        self.last_assigned_incident_id = assignment_receipt.incident_id

        triage_receipt = await self.assignee.triage_incident(
            taken_in_charge,
            detector_context=detector_context,
        )
        triaged = await self.workflow.mark_triaged(
            triage_receipt,
            agent_role=assignment_receipt.agent_role,
            agent_jid=assignment_receipt.agent_jid,
        )
        self.triaged_count += 1
        self.last_triaged_incident_id = triage_receipt.incident_id

        LOGGER.info(
            "Incident=%s takeover and BDI triage completed; primary investigator=%s",
            triage_receipt.incident_id,
            triage_receipt.primary_investigator,
        )
        return IncidentWorkflowResult(
            incident=triaged,
            created=True,
            correlated=False,
        )
