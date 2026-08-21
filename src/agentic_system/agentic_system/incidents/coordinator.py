from __future__ import annotations

import asyncio
import logging
from typing import Any

from .anomalies import AnomalyObservation
from .contracts import DetectorContextPort, IncidentAssigneePort, IncidentRepositoryPort
from .models import IncidentWorkflowResult
from .tasks import AgentTaskState, AgentTaskWorkflow, normalize_task_state
from .workflow import IncidentWorkflow


LOGGER = logging.getLogger("agentic_system.incidents.coordinator")
RECOVERABLE_INCIDENT_STATUSES = ("NEW", "TAKEN_IN_CHARGE", "TRIAGED")
AUTONOMOUS_WORKFLOW_TERMINAL_STATUSES = {
    "RESOLVED",
    "CLOSED",
    "OPERATOR_ACTION_REQUIRED",
}
WORKFLOW_COMPLETION_POLL_SECONDS = 0.5
TASK_DISPATCH_RETRY_DELAY_SECONDS = 2.0
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
        self.tasks_dispatched_count = 0
        self.tasks_running_count = 0
        self.last_assigned_incident_id: str | None = None
        self.last_triaged_incident_id: str | None = None
        self.last_task_id: str | None = None
        self.last_dispatched_task_id: str | None = None

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
        """Own one anomaly until the complete autonomous workflow can be released."""

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
        """Translate incomplete durable incidents into FIFO work items oldest-first."""

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
        """Keep exclusive FIFO ownership while durable agent work is non-terminal."""

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
                task_state = normalize_task_state(task.get("state") or "")

                if task_state == AgentTaskState.FAILED:
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

                if task_state in {
                    AgentTaskState.PENDING,
                    AgentTaskState.RETRYING,
                    AgentTaskState.DISPATCHED,
                }:
                    await self._advance_investigation_task(incident)
                    await asyncio.sleep(TASK_DISPATCH_RETRY_DELAY_SECONDS)
                    continue

            await asyncio.sleep(poll_interval_seconds)

    async def recover_incomplete_incidents(self) -> dict[str, int]:
        """Compatibility recovery helper used by isolated tests."""

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
            task_id = str(agentic.get("investigation_task_id") or "").strip()

            if not task_id:
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

            current = await self._advance_investigation_task(current)

        return current

    async def _advance_investigation_task(
        self,
        incident: dict[str, Any],
    ) -> dict[str, Any]:
        """Advance durable task through XMPP acceptance to RUNNING ownership."""

        current = dict(incident)
        incident_id = str(current.get("incident_id") or "").strip()
        task_id = str(
            (current.get("agentic") or {}).get("investigation_task_id") or ""
        ).strip()
        if not task_id:
            return current

        task = await self.repository.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Incident {incident_id} references missing task {task_id}")
        state = normalize_task_state(task["state"])

        # DISPATCHED without a confirmed transition to RUNNING is an uncertain
        # delivery state (for example DB failure after an XMPP AGREE). Convert it
        # to RETRYING and rely on the stable task_id for idempotent redelivery.
        if state == AgentTaskState.DISPATCHED:
            recovered = await self.task_workflow.mark_execution_failed(
                task_id,
                error_type="dispatch_state_uncertain",
                message=(
                    "Task remained DISPATCHED without durable RUNNING ownership; "
                    "scheduling an idempotent specialist redelivery."
                ),
                retryable=True,
            )
            LOGGER.warning(
                "Recovered uncertain DISPATCHED task=%s into state=%s",
                task_id,
                recovered.get("state"),
            )
            return current

        if state not in {AgentTaskState.PENDING, AgentTaskState.RETRYING}:
            return current

        dispatched = await self.task_workflow.mark_dispatched(task_id)
        dispatched_state = normalize_task_state(dispatched["state"])
        if dispatched_state == AgentTaskState.FAILED:
            return current

        try:
            receipt = await self.assignee.dispatch_investigation_task(current, dispatched)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed = await self.task_workflow.mark_execution_failed(
                task_id,
                error_type="specialist_dispatch_failed",
                message=str(exc),
                retryable=True,
            )
            LOGGER.warning(
                "Task dispatch failed without killing agents: task=%s state=%s error=%s",
                task_id,
                failed.get("state"),
                exc,
            )
            return current

        running = await self.task_workflow.mark_running(task_id)
        self.tasks_dispatched_count += 1
        self.tasks_running_count += 1
        self.last_dispatched_task_id = task_id

        updated = await self.repository.update_incident(
            incident_id,
            {
                "agentic": {
                    "current_agent": receipt.agent_role,
                    "active_agents": ["technical_lead", receipt.agent_role],
                    "task_dispatch_correlation_id": receipt.correlation_id,
                    "specialist_bdi_goal": receipt.bdi_goal,
                    "specialist_bdi_acceptance_intention": receipt.bdi_acceptance_intention,
                    "specialist_bdi_intention": receipt.bdi_investigation_intention,
                }
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared after specialist accepted task: {incident_id}"
            )

        event = await self.repository.add_event(
            incident_id,
            {
                "event_type": "INVESTIGATION_TASK_DISPATCHED",
                "agent_role": receipt.agent_role,
                "agent_jid": receipt.agent_jid,
                "action": "accept_investigation_task",
                "reason": (
                    "Technical Lead delegated the durable task and the selected "
                    "specialist committed its BDI investigation intention."
                ),
                "status": updated.get("status", "TRIAGED"),
                "task_id": task_id,
                "task_state": running.get("state"),
                "correlation_id": receipt.correlation_id,
                "bdi_goal": receipt.bdi_goal,
                "bdi_intention": receipt.bdi_investigation_intention,
                "outcome": receipt.agent_role,
            },
        )
        if event is None:
            raise RuntimeError(
                f"Could not persist specialist dispatch event for incident: {incident_id}"
            )

        LOGGER.warning(
            "Task=%s accepted by %s and entered RUNNING with BDI intention=%s",
            task_id,
            receipt.agent_role,
            receipt.bdi_investigation_intention,
        )
        return dict(updated)

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
    def _progress_marker(incident: dict[str, Any]) -> tuple[str, str | None, str | None]:
        agentic = dict(incident.get("agentic") or {})
        return (
            str(incident.get("status") or "").upper(),
            str(agentic.get("investigation_task_id"))
            if agentic.get("investigation_task_id")
            else None,
            str(agentic.get("specialist_bdi_intention"))
            if agentic.get("specialist_bdi_intention")
            else None,
        )

    @staticmethod
    def _safe_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
