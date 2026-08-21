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
    """Coordinate specialist ReAct work and the Technical Lead BDI critic cycle."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.react_results_completed_count = 0
        self.react_results_failed_count = 0
        self.technical_lead_reviews_completed_count = 0
        self.technical_lead_reviews_failed_count = 0

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
                    self._apply_agent_activity(
                        incident_id,
                        decision="operator_action_required",
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
                    collector = getattr(self.assignee, "collect_investigation_result", None)
                    if collector is not None:
                        try:
                            receipt = await collector(incident, task)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            await self._persist_result_collection_failure(
                                incident,
                                task,
                                error=exc,
                            )
                            continue

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
                    "review_state": "PENDING",
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

        try:
            review = await self._review_specialist_result(updated, receipt)
            await self._persist_technical_lead_review(
                updated,
                receipt,
                review,
                task_id=task_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.technical_lead_reviews_failed_count += 1
            LOGGER.exception(
                "Technical Lead review failed; escalating without changing agent health: "
                "incident=%s error=%s",
                incident_id,
                exc,
            )
            await self.repository.update_incident(
                incident_id,
                {
                    "status": "OPERATOR_ACTION_REQUIRED",
                    "validation": {
                        "status": "TECHNICAL_LEAD_REVIEW_FAILED",
                        "summary": "Specialist evidence was collected, but the Technical Lead critic cycle failed.",
                    },
                    "agentic": {
                        "current_agent": "technical_lead",
                        "active_agents": [],
                        "review_state": "FAILED",
                    },
                },
            )
            await self.repository.add_event(
                incident_id,
                {
                    "event_type": "TECHNICAL_LEAD_REVIEW_FAILED",
                    "agent_role": "technical_lead",
                    "action": "escalate_review_failure",
                    "reason": str(exc),
                    "status": "OPERATOR_ACTION_REQUIRED",
                    "task_id": task_id,
                },
            )
            self._apply_agent_activity(
                incident_id,
                decision="operator_action_required",
            )

    async def _review_specialist_result(
        self,
        incident: dict[str, Any],
        receipt: Any,
    ) -> Any:
        reviewer = getattr(self.assignee, "review_investigation_result", None)
        if callable(reviewer):
            return await reviewer(incident, receipt)

        # AgentRuntime already owns the Technical Lead instance; keep the review
        # inside that SPADE agent while the formal runtime port is introduced.
        technical_lead_getter = getattr(self.assignee, "_technical_lead", None)
        if not callable(technical_lead_getter):
            raise RuntimeError("Technical Lead review capability is not available")
        technical_lead = technical_lead_getter()
        return await technical_lead.review_specialist_result(
            incident,
            specialist_result=receipt.task_outcome(),
        )

    async def _persist_technical_lead_review(
        self,
        incident: dict[str, Any],
        specialist_receipt: Any,
        review: Any,
        *,
        task_id: str,
    ) -> None:
        incident_id = str(incident["incident_id"])
        decision = str(review.decision).strip().lower()
        if decision not in {"resolve", "operator_action_required", "request_support"}:
            raise RuntimeError(f"Unsupported Technical Lead review decision: {decision}")

        evidence = [
            str(item).strip()
            for item in specialist_receipt.findings
            if str(item).strip()
        ]
        diagnosis = {
            "summary": review.diagnosis_summary,
            "root_cause": review.root_cause,
            "confidence": review.confidence,
            "evidence": evidence,
        }
        remediation = {
            "summary": review.remediation_summary,
            "steps": list(review.remediation_steps),
            "status": "ADVISORY",
        }

        if decision == "resolve":
            status = "RESOLVED"
            validation = {
                "status": "EVIDENCE_REVIEWED",
                "summary": (
                    "The Technical Lead accepted the specialist evidence and determined "
                    "that no immediate operator action is required."
                ),
            }
            active_agents: list[str] = []
        elif decision == "operator_action_required":
            status = "OPERATOR_ACTION_REQUIRED"
            validation = {
                "status": "OPERATOR_ACTION_PENDING",
                "summary": (
                    "The Technical Lead accepted the diagnosis, but remediation requires "
                    "human operator action."
                ),
            }
            active_agents = []
        else:
            status = "UNDER_ANALYSIS"
            validation = {
                "status": "MORE_EVIDENCE_REQUIRED",
                "summary": (
                    "The Technical Lead requested evidence from another technical domain "
                    "before accepting a final diagnosis."
                ),
            }
            active_agents = ["technical_lead"]

        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": status,
                "diagnosis": diagnosis,
                "remediation": remediation,
                "validation": validation,
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": active_agents,
                    "review_state": "COMPLETED",
                    "review_decision": decision,
                    "review_confidence": review.confidence,
                    "review_rationale": review.rationale,
                    "bdi_review_goal": review.bdi_goal,
                    "bdi_review_intention": review.bdi_review_intention,
                    "bdi_review_decision_intention": review.bdi_decision_intention,
                    "support_requested": decision == "request_support",
                    "support_domain": review.support_domain,
                    "support_reason": review.support_reason,
                },
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared while persisting Technical Lead review: {incident_id}"
            )

        await self.repository.add_event(
            incident_id,
            {
                "event_type": "TECHNICAL_LEAD_REVIEW_COMPLETED",
                "agent_role": "technical_lead",
                "action": "review_specialist_result",
                "reason": review.rationale,
                "status": status,
                "task_id": task_id,
                "outcome": {
                    "decision": decision,
                    "confidence": review.confidence,
                    "diagnosis_summary": review.diagnosis_summary,
                    "support_domain": review.support_domain,
                },
            },
        )
        self.technical_lead_reviews_completed_count += 1
        self._apply_agent_activity(incident_id, decision=decision)
        LOGGER.warning(
            "Technical Lead BDI review persisted: incident=%s decision=%s status=%s "
            "confidence=%.3f",
            incident_id,
            decision,
            status,
            review.confidence,
        )

    def _apply_agent_activity(self, incident_id: str, *, decision: str) -> None:
        agents = getattr(self.assignee, "agents", None)
        if not isinstance(agents, list):
            return
        for agent in agents:
            if getattr(agent, "activity_incident_id", None) != incident_id:
                continue
            role = str(getattr(agent, "role", ""))
            if decision == "request_support" and role == "technical_lead":
                agent.set_activity(
                    "WAITING",
                    incident_id=incident_id,
                    detail="support_coordination_pending",
                )
            else:
                agent.set_activity("IDLE")

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

    async def _persist_result_collection_failure(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
        *,
        error: Exception,
    ) -> None:
        task_id = str(task["task_id"])
        incident_id = str(incident["incident_id"])
        failed = await self.task_workflow.mark_execution_failed(
            task_id,
            error_type="invalid_specialist_result",
            message=str(error),
            retryable=True,
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
                "event_type": "SPECIALIST_RESULT_INVALID",
                "agent_role": str(task.get("assigned_to") or "specialist"),
                "action": "reject_invalid_specialist_result",
                "reason": str(error),
                "status": incident.get("status", "TRIAGED"),
                "task_id": task_id,
                "outcome": {"task_state": failed.get("state"), "retryable": True},
            },
        )
        self.react_results_failed_count += 1
        LOGGER.warning(
            "Invalid specialist result converted into task retry: incident=%s task=%s state=%s error=%s",
            incident_id,
            task_id,
            failed.get("state"),
            error,
        )
