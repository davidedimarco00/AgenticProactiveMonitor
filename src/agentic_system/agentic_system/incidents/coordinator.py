from __future__ import annotations

import logging
from typing import Any

from .anomalies import AnomalyObservation
from .contracts import DetectorContextPort, IncidentAssigneePort, IncidentRepositoryPort
from .models import IncidentWorkflowResult
from .tasks import AgentTaskWorkflow
from .workflow import IncidentWorkflow


LOGGER = logging.getLogger("agentic_system.incidents.coordinator")
RECOVERABLE_INCIDENT_STATUSES = ("NEW", "TAKEN_IN_CHARGE", "TRIAGED")


class IncidentCoordinator:
    """Orchestrate durable incident progress without coupling failures to agent life."""

    def __init__(
        self,
        workflow: IncidentWorkflow,
        assignee: IncidentAssigneePort,
        detector_context: DetectorContextPort,
        task_workflow: AgentTaskWorkflow,
        repository: IncidentRepositoryPort,
    ) -> None:
        self.workflow = workflow
        self.assignee = assignee
        self.detector_context = detector_context
        self.task_workflow = task_workflow
        self.repository = repository
        self.assigned_count = 0
        self.triaged_count = 0
        self.tasks_created_count = 0
        self.last_assigned_incident_id: str | None = None
        self.last_triaged_incident_id: str | None = None
        self.last_task_id: str | None = None

    async def handle_anomaly(
        self,
        observation: AnomalyObservation,
    ) -> IncidentWorkflowResult:
        detector_context = await self._resolve_detector_context(observation.detector_id)
        result = await self.workflow.handle_anomaly(
            observation,
            detector_context=detector_context,
        )

        # The state-driven continuation is intentionally executed for both a new
        # anomaly and a correlated re-observation. If a previous attempt failed
        # after persistence, the next delivery resumes from the last durable
        # incident state instead of silently abandoning the workflow.
        resumed = await self._resume_incident(result.incident, detector_context)
        return IncidentWorkflowResult(
            incident=resumed,
            created=result.created,
            correlated=result.correlated,
        )

    async def recover_incomplete_incidents(self) -> dict[str, int]:
        """Resume persisted NEW/TAKEN_IN_CHARGE/TRIAGED incidents after restart."""

        scanned = 0
        resumed = 0
        failed = 0
        seen_incident_ids: set[str] = set()
        for status in RECOVERABLE_INCIDENT_STATUSES:
            incidents = await self.repository.list_incidents(status=status, limit=500)
            for incident in incidents:
                incident_id = str(incident.get("incident_id") or "")
                if not incident_id or incident_id in seen_incident_ids:
                    continue
                seen_incident_ids.add(incident_id)
                scanned += 1

                detector_id = str((incident.get("anomaly") or {}).get("detector_id") or "")
                detector_context = await self._resolve_detector_context(detector_id)
                before = self._progress_marker(incident)
                try:
                    recovered = await self._resume_incident(incident, detector_context)
                except Exception as exc:
                    failed += 1
                    LOGGER.exception(
                        "Could not recover persisted incident=%s from state=%s: %s",
                        incident_id,
                        incident.get("status"),
                        exc,
                    )
                    continue
                if self._progress_marker(recovered) != before:
                    resumed += 1

        if scanned:
            LOGGER.warning(
                "Incident recovery scan completed: scanned=%d resumed=%d failed=%d",
                scanned,
                resumed,
                failed,
            )
        return {"scanned": scanned, "resumed": resumed, "failed": failed}

    async def _resume_incident(
        self,
        incident: dict[str, Any],
        detector_context: dict[str, Any],
    ) -> dict[str, Any]:
        current = dict(incident)
        technical_lead_jid = "technical-lead@xmpp"

        # Each transition is persisted before the next external interaction.
        # Re-entering this method therefore continues from the latest durable
        # state and does not repeat already completed workflow stages.
        if str(current.get("status") or "").upper() == "NEW":
            assignment_receipt = await self.assignee.assign_incident(current)
            technical_lead_jid = assignment_receipt.agent_jid
            current = dict(
                await self.workflow.mark_taken_in_charge(
                    assignment_receipt.incident_id,
                    agent_role=assignment_receipt.agent_role,
                    agent_jid=assignment_receipt.agent_jid,
                )
            )
            self.assigned_count += 1
            self.last_assigned_incident_id = assignment_receipt.incident_id

        if str(current.get("status") or "").upper() == "TAKEN_IN_CHARGE":
            triage_receipt = await self.assignee.triage_incident(
                current,
                detector_context=detector_context,
            )
            current = dict(
                await self.workflow.mark_triaged(
                    triage_receipt,
                    agent_role="technical_lead",
                    agent_jid=technical_lead_jid,
                )
            )
            self.triaged_count += 1
            self.last_triaged_incident_id = triage_receipt.incident_id

        if str(current.get("status") or "").upper() == "TRIAGED":
            agentic = dict(current.get("agentic") or {})
            if agentic.get("investigation_task_id"):
                return current

            primary_investigator = str(
                agentic.get("primary_investigator") or ""
            ).strip().lower()
            if not primary_investigator:
                raise RuntimeError(
                    f"Triaged incident {current.get('incident_id')} has no primary investigator"
                )

            task = await self.task_workflow.create_investigation_task(
                current,
                primary_investigator=primary_investigator,
            )
            current = dict(
                await self.workflow.mark_investigation_task_created(
                    str(current["incident_id"]),
                    task_id=str(task["task_id"]),
                    primary_investigator=primary_investigator,
                )
            )
            self.tasks_created_count += 1
            self.last_task_id = str(task["task_id"])
            LOGGER.warning(
                "Incident=%s now owns durable task=%s state=%s assigned_to=%s",
                current["incident_id"],
                task["task_id"],
                task["state"],
                primary_investigator,
            )

        return current

    async def _resolve_detector_context(self, detector_id: str) -> dict[str, Any]:
        if not detector_id:
            return self._fallback_detector_context(detector_id)
        try:
            return await self.detector_context.get_detector_context(detector_id)
        except Exception as exc:
            LOGGER.warning(
                "Could not resolve OpenSearch detector metadata for %s: %s",
                detector_id,
                exc,
            )
            return self._fallback_detector_context(detector_id)

    @staticmethod
    def _fallback_detector_context(detector_id: str) -> dict[str, Any]:
        return {
            "detector_id": detector_id,
            "detector_type": "SINGLE_ENTITY",
            "name": "unknown",
            "description": "",
            "indices": [],
        }

    @staticmethod
    def _progress_marker(incident: dict[str, Any]) -> tuple[str, str | None]:
        agentic = dict(incident.get("agentic") or {})
        return (
            str(incident.get("status") or "").upper(),
            str(agentic.get("investigation_task_id"))
            if agentic.get("investigation_task_id")
            else None,
        )
