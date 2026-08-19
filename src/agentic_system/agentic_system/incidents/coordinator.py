from __future__ import annotations

import asyncio
import logging
from typing import Any

from .anomalies import AnomalyObservation
from .contracts import DetectorContextPort, IncidentAssigneePort, IncidentRepositoryPort
from .models import IncidentWorkflowResult
from .tasks import AgentTaskState, AgentTaskWorkflow
from .workflow import IncidentWorkflow


LOGGER = logging.getLogger("agentic_system.incidents.coordinator")
RECOVERABLE_INCIDENT_STATUSES = ("NEW", "TAKEN_IN_CHARGE", "TRIAGED")
AUTONOMOUS_WORKFLOW_TERMINAL_STATUSES = {
    "RESOLVED",
    "CLOSED",
    "OPERATOR_ACTION_REQUIRED",
}
WORKFLOW_COMPLETION_POLL_SECONDS = 0.5
RECOVERY_RESULT_INDEX = "agentic-workflow-recovery"


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
        """Advance one anomaly to the latest durable workflow state.

        Recovery observations bypass anomaly correlation and resume the exact
        persisted incident encoded by `recovery_incident_id`. Fresh OpenSearch
        observations continue through normal SINGLE_ENTITY correlation.
        """

        if observation.recovery_incident_id:
            incident = await self.repository.get_incident(observation.recovery_incident_id)
            if incident is None:
                raise RuntimeError(
                    "Persisted incident disappeared before FIFO recovery: "
                    f"{observation.recovery_incident_id}"
                )
            detector_context = await self._resolve_detector_context(observation.detector_id)
            resumed = await self._resume_incident(incident, detector_context)
            LOGGER.warning(
                "Recovered incident=%s through the exclusive anomaly FIFO",
                observation.recovery_incident_id,
            )
            return IncidentWorkflowResult(
                incident=resumed,
                created=False,
                correlated=False,
            )

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

    async def handle_anomaly_exclusively(
        self,
        observation: AnomalyObservation,
    ) -> IncidentWorkflowResult:
        """Own one anomaly until the complete autonomous workflow can be released.

        `AnomalyIntake` has exactly one consumer and awaits this method. Keeping
        this coroutine pending therefore keeps the current anomaly ACTIVE and all
        subsequent OpenSearch anomalies physically queued. Creating a PENDING
        specialist task is not completion: the FIFO slot is released only after
        the incident reaches a terminal autonomous status.
        """

        result = await self.handle_anomaly(observation)
        terminal_incident = await self.wait_until_workflow_terminal(
            str(result.incident["incident_id"])
        )
        return IncidentWorkflowResult(
            incident=terminal_incident,
            created=result.created,
            correlated=result.correlated,
        )

    async def build_recovery_observations(self) -> list[AnomalyObservation]:
        """Translate incomplete durable incidents into FIFO work items.

        Startup recovery must obey the same single-active policy as fresh anomaly
        processing. Therefore incomplete incidents are not resumed in parallel;
        they are converted to synthetic observations, ordered oldest first, and
        seeded into the global queue before the OpenSearch watcher starts.
        """

        incidents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for status in RECOVERABLE_INCIDENT_STATUSES:
            for incident in await self.repository.list_incidents(status=status, limit=500):
                incident_id = str(incident.get("incident_id") or "").strip()
                if not incident_id or incident_id in seen:
                    continue
                seen.add(incident_id)
                incidents.append(incident)

        incidents.sort(
            key=lambda item: str(item.get("created_at") or item.get("updated_at") or "")
        )
        observations: list[AnomalyObservation] = []
        for incident in incidents:
            incident_id = str(incident["incident_id"])
            anomaly = dict(incident.get("anomaly") or {})
            detector_id = str(anomaly.get("detector_id") or "").strip()
            if not detector_id:
                LOGGER.warning(
                    "Skipping durable recovery for incident=%s without detector_id",
                    incident_id,
                )
                continue

            observations.append(
                AnomalyObservation(
                    result_id=f"recover:{incident_id}",
                    result_index=RECOVERY_RESULT_INDEX,
                    detector_id=detector_id,
                    anomaly_grade=self._safe_float(anomaly.get("grade"), default=0.0),
                    confidence=self._safe_float(anomaly.get("confidence"), default=0.0),
                    anomaly_score=None,
                    data_start_time=None,
                    data_end_time=None,
                    execution_start_time=None,
                    execution_end_time=None,
                    recovery_incident_id=incident_id,
                )
            )

        return observations

    async def wait_until_workflow_terminal(
        self,
        incident_id: str,
        *,
        poll_interval_seconds: float = WORKFLOW_COMPLETION_POLL_SECONDS,
    ) -> dict[str, Any]:
        """Wait without blocking the event loop until one incident can release the FIFO.

        A PENDING/DISPATCHED/RUNNING/RETRYING task keeps exclusive ownership of
        the anomaly. A terminal task failure is escalated to
        OPERATOR_ACTION_REQUIRED so the failed work item does not poison the
        global queue and the agents remain available for the next anomaly.
        """

        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")

        while True:
            incident = await self.repository.get_incident(incident_id)
            if incident is None:
                raise RuntimeError(
                    f"Incident disappeared while waiting for workflow completion: {incident_id}"
                )

            status = str(incident.get("status") or "").upper()
            if status in AUTONOMOUS_WORKFLOW_TERMINAL_STATUSES:
                LOGGER.info(
                    "Exclusive anomaly workflow released: incident=%s status=%s",
                    incident_id,
                    status,
                )
                return incident

            task_id = str(
                (incident.get("agentic") or {}).get("investigation_task_id") or ""
            ).strip()
            if task_id:
                task = await self.repository.get_task(task_id)
                if task is None:
                    raise RuntimeError(
                        f"Incident {incident_id} references missing agent task {task_id}"
                    )
                task_state = str(task.get("state") or "").upper()
                if task_state == AgentTaskState.FAILED.value:
                    last_error = task.get("last_error") or {}
                    reason = str(last_error.get("message") or "Agent task failed.")
                    escalated = await self.workflow.mark_operator_action_required(
                        incident_id,
                        task_id=task_id,
                        reason=reason,
                    )
                    LOGGER.warning(
                        "Terminal task failure released exclusive workflow via operator escalation: "
                        "incident=%s task=%s",
                        incident_id,
                        task_id,
                    )
                    return escalated

            # PENDING is intentionally included here. Until specialist dispatch
            # and the downstream collaborative stages are implemented, this is
            # the correct visible state: the first anomaly remains ACTIVE and
            # later anomalies remain in the FIFO instead of being triaged early.
            await asyncio.sleep(poll_interval_seconds)

    async def recover_incomplete_incidents(self) -> dict[str, int]:
        """Compatibility recovery helper used by isolated tests.

        Runtime startup now uses `build_recovery_observations()` so recovery is
        serialized through the global FIFO. This direct helper remains useful for
        unit tests that validate state-driven durable resumption in isolation.
        """

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

    @staticmethod
    def _safe_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
