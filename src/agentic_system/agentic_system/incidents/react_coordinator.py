from __future__ import annotations

import asyncio
import logging
from typing import Any

from .coordinator import (
    AUTONOMOUS_WORKFLOW_TERMINAL_STATUSES,
    TASK_DISPATCH_RETRY_DELAY_SECONDS,
    WORKFLOW_COMPLETION_POLL_SECONDS,
    IncidentCoordinator,
)
from .tasks import AgentTaskState, normalize_task_state


LOGGER = logging.getLogger("agentic_system.incidents.react_coordinator")


class ReActIncidentCoordinator(IncidentCoordinator):
    """Extend durable incident coordination with specialist ReAct outcomes.

    A completed specialist task does not close the incident. The result returns to
    the Technical Lead and the incident enters UNDER_ANALYSIS, preserving the
    architectural separation between specialist diagnosis proposals and the later
    Technical Lead critic/coordination decision.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.react_results_completed_count = 0
        self.react_results_failed_count = 0

    async def wait_until_workflow_terminal(
        self,
        incident_id: str,
        *,
        poll_interval_seconds: float = WORKFLOW_COMPLETION_POLL_SECONDS,
    ) -> dict[str, Any]:
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

                if task_state == AgentTaskState.RUNNING:
                    receipt = await self.assignee.collect_investigation_result(
                        incident,
                        task,
                    )
                    if receipt is not None:
                        if receipt.succeeded:
                            await self._persist_successful_react_result(
                                incident,
                                task,
                                receipt,
                            )
                        else:
                            await self._persist_failed_react_result(
                                incident,
                                task,
                                receipt,
                            )
                        continue

            await asyncio.sleep(poll_interval_seconds)

    async def _persist_successful_react_result(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
        receipt: Any,
    ) -> None:
        task_id = str(task["task_id"])
        incident_id = str(incident["incident_id"])
        outcome = receipt.task_outcome()

        completed = await self.task_workflow.mark_completed(
            task_id,
            outcome={
                "status": "completed",
                "summary": receipt.summary,
                "result_ref": receipt.conversation_id,
            },
        )
        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": "UNDER_ANALYSIS",
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": ["technical_lead"],
                    "task_state": completed.get("state"),
                },
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared while persisting ReAct result: {incident_id}"
            )

        event = await self.repository.add_event(
            incident_id,
            {
                "event_type": "SPECIALIST_INVESTIGATION_COMPLETED",
                "agent_role": receipt.agent_role,
                "agent_jid": receipt.agent_jid,
                "action": "return_react_investigation_result",
                "reason": (
                    "The specialist completed its BDI investigate_incident intention "
                    "through a bounded ReAct loop and returned evidence to the Technical Lead."
                ),
                "status": "UNDER_ANALYSIS",
                "task_id": task_id,
                "outcome": outcome,
            },
        )
        if event is None:
            raise RuntimeError(
                f"Could not persist ReAct completion event for incident: {incident_id}"
            )

        self.react_results_completed_count += 1
        LOGGER.warning(
            "ReAct result persisted: incident=%s task=%s specialist=%s confidence=%.3f "
            "steps=%d assistance=%s",
            incident_id,
            task_id,
            receipt.agent_role,
            receipt.confidence,
            receipt.react_steps,
            receipt.assistance_domain if receipt.assistance_required else "none",
        )

    async def _persist_failed_react_result(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
        receipt: Any,
    ) -> None:
        task_id = str(task["task_id"])
        incident_id = str(incident["incident_id"])
        failed = await self.task_workflow.mark_execution_failed(
            task_id,
            error_type="specialist_react_failed",
            message=receipt.error or "Specialist ReAct execution failed.",
            retryable=receipt.retryable,
        )
        await self.repository.update_incident(
            incident_id,
            {
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": ["technical_lead"],
                    "task_state": failed.get("state"),
                }
            },
        )
        await self.repository.add_event(
            incident_id,
            {
                "event_type": "SPECIALIST_INVESTIGATION_FAILED",
                "agent_role": receipt.agent_role,
                "agent_jid": receipt.agent_jid,
                "action": "handle_react_failure",
                "reason": receipt.error or "Specialist ReAct execution failed.",
                "status": incident.get("status", "TRIAGED"),
                "task_id": task_id,
                "outcome": {
                    "task_state": failed.get("state"),
                    "retryable": receipt.retryable,
                },
            },
        )
        self.react_results_failed_count += 1
        LOGGER.warning(
            "ReAct execution failure persisted without failing agent health: "
            "incident=%s task=%s state=%s error=%s",
            incident_id,
            task_id,
            failed.get("state"),
            receipt.error,
        )
