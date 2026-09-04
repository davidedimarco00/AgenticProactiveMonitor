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
    """Coordinate specialist ReAct work, peer collaboration and TL BDI review."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.react_results_completed_count = 0
        self.react_results_failed_count = 0
        self.technical_lead_reviews_completed_count = 0
        self.technical_lead_reviews_failed_count = 0
        self.peer_consultations_logged_count = 0

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
                    resolved = await self._persist_terminal_diagnostic_failure(
                        incident,
                        task,
                        reason=reason,
                    )
                    LOGGER.warning(
                        "Terminal task failure released exclusive workflow with a diagnostic "
                        "failure result instead of OPERATOR_ACTION_REQUIRED: incident=%s task=%s",
                        incident_id,
                        task_id,
                    )
                    return resolved

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
        involved_roles = await self._involved_specialist_roles(incident_id)

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
                    "active_agents": ["technical_lead", *involved_roles],
                    "task_state": completed.get("state"),
                    "review_state": "PENDING",
                    "collaboration_roles": involved_roles,
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
                    "The specialist completed its BDI investigation intention through a "
                    "bounded ReAct loop and returned structured evidence for team review."
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

        await self._log_peer_consultation(incident_id, receipt, task_id=task_id)

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
            await self._persist_technical_lead_review_failure(
                incident_id,
                specialist_receipt=receipt,
                task_id=task_id,
                error=exc,
            )
            return

    async def _log_peer_consultation(
        self,
        incident_id: str,
        receipt: Any,
        *,
        task_id: str,
    ) -> None:
        """Record an autonomous specialist-to-specialist consultation centrally.

        The requesting specialist already contacted the peer directly and folded
        the evidence in; this only preserves the audit trail on the incident.
        """

        consultation = getattr(receipt, "peer_consultation", None)
        if not isinstance(consultation, dict) or not consultation.get("requested"):
            return

        await self.repository.add_event(
            incident_id,
            {
                "event_type": "PEER_CONSULTATION",
                "agent_role": receipt.agent_role,
                "agent_jid": receipt.agent_jid,
                "action": "autonomous_peer_consultation",
                "reason": str(consultation.get("reason") or ""),
                "status": "UNDER_ANALYSIS",
                "task_id": task_id,
                "outcome": {
                    "from_role": receipt.agent_role,
                    "to_role": consultation.get("target_role"),
                    "status": consultation.get("status"),
                    "peer_confidence": consultation.get("peer_confidence"),
                    "peer_findings_count": consultation.get("peer_findings_count"),
                },
            },
        )
        self.peer_consultations_logged_count += 1
        LOGGER.warning(
            "Autonomous peer consultation logged: incident=%s %s -> %s status=%s",
            incident_id,
            receipt.agent_role,
            consultation.get("target_role"),
            consultation.get("status"),
        )

    async def _review_specialist_result(
        self,
        incident: dict[str, Any],
        receipt: Any,
    ) -> Any:
        incident_id = str(incident["incident_id"])
        result_payload = receipt.task_outcome()
        result_payload["specialists_already_involved"] = await self._involved_specialist_roles(
            incident_id
        )
        collaboration_history = await self._collaboration_history(
            incident_id,
            exclude_task_id=receipt.task_id,
        )
        if collaboration_history:
            result_payload["collaboration_history"] = collaboration_history

        reviewer = getattr(self.assignee, "review_investigation_result", None)
        technical_lead_getter = getattr(self.assignee, "_technical_lead", None)
        if callable(technical_lead_getter):
            technical_lead = technical_lead_getter()
            return await technical_lead.review_specialist_result(
                incident,
                specialist_result=result_payload,
            )
        if callable(reviewer):
            return await reviewer(incident, receipt)
        raise RuntimeError("Technical Lead review capability is not available")

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

        # Defense in depth. Neither an operator escalation nor a Technical Lead
        # authorized support round is a state of the autonomous pipeline: peer
        # collaboration happens between specialists before the review, and human
        # actions stay advisory remediation attached to a resolved diagnosis.
        if decision in {"operator_action_required", "request_support"}:
            LOGGER.error(
                "Technical Lead returned legacy decision %s; normalizing to resolve: incident=%s",
                decision,
                incident_id,
            )
            decision = "resolve"

        involved_roles = await self._involved_specialist_roles(incident_id)
        evidence = await self._combined_findings(
            incident_id,
            current_findings=specialist_receipt.findings,
        )
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

        status = "RESOLVED"
        validation = {
            "status": "EVIDENCE_REVIEWED",
            "summary": (
                "The Technical Lead accepted the combined specialist evidence and preserved "
                "the best evidence-backed diagnosis. Any human actions are advisory only."
            ),
        }

        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": status,
                "diagnosis": diagnosis,
                "remediation": remediation,
                "validation": validation,
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": [],
                    "review_state": "COMPLETED",
                    "review_decision": decision,
                    "review_confidence": review.confidence,
                    "review_rationale": review.rationale,
                    "bdi_review_goal": review.bdi_goal,
                    "bdi_review_intention": review.bdi_review_intention,
                    "bdi_review_decision_intention": review.bdi_decision_intention,
                    "collaboration_roles": involved_roles,
                    "collaboration_round": max(len(involved_roles) - 1, 0),
                    "peer_collaboration_state": "COMPLETED",
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
                    "specialists_involved": involved_roles,
                },
            },
        )
        self.technical_lead_reviews_completed_count += 1
        self._release_agents(incident_id)
        LOGGER.warning(
            "Technical Lead BDI review persisted: incident=%s decision=%s status=%s "
            "confidence=%.3f specialists=%s",
            incident_id,
            decision,
            status,
            review.confidence,
            ",".join(involved_roles) or "none",
        )

    async def _advance_investigation_task(
        self,
        incident: dict[str, Any],
    ) -> dict[str, Any]:
        updated = await super()._advance_investigation_task(incident)
        if str(updated.get("status") or "").upper() != "UNDER_ANALYSIS":
            return updated

        agentic = dict(updated.get("agentic") or {})
        if str(agentic.get("peer_collaboration_state") or "").upper() != "ACTIVE":
            return updated

        roles = await self._involved_specialist_roles(str(updated["incident_id"]))
        task_id = str(agentic.get("investigation_task_id") or "").strip()
        task = await self.repository.get_task(task_id) if task_id else None
        current_agent = (
            str((task or {}).get("assigned_to") or agentic.get("current_agent") or "technical_lead")
            .strip()
            .lower()
        )
        synchronized = await self.repository.update_incident(
            str(updated["incident_id"]),
            {
                "agentic": {
                    "current_agent": current_agent,
                    "active_agents": ["technical_lead", *roles],
                    "collaboration_roles": roles,
                    "collaboration_round": max(len(roles) - 1, 1),
                    "peer_collaboration_state": "ACTIVE",
                }
            },
        )
        return dict(synchronized or updated)

    async def _involved_specialist_roles(self, incident_id: str) -> list[str]:
        tasks = await self.task_workflow.repository.list_tasks(
            incident_id=incident_id,
            limit=20,
        )
        roles: list[str] = []
        for task in tasks:
            role = str(task.get("assigned_to") or "").strip().lower()
            if role and role not in roles:
                roles.append(role)
        return roles

    async def _collaboration_history(
        self,
        incident_id: str,
        *,
        exclude_task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        list_events = getattr(self.repository, "list_events", None)
        if not callable(list_events):
            return []
        events = await list_events(
            incident_id=incident_id,
            limit=200,
            ascending=True,
        )
        history: list[dict[str, Any]] = []
        for event in events:
            if str(event.get("event_type") or "").upper() != "SPECIALIST_INVESTIGATION_COMPLETED":
                continue
            task_id = str(event.get("task_id") or "").strip()
            if exclude_task_id and task_id == exclude_task_id:
                continue
            outcome = event.get("outcome") or {}
            if not isinstance(outcome, dict):
                continue
            history.append(
                {
                    "task_id": task_id,
                    "agent_role": str(event.get("agent_role") or "").strip().lower(),
                    "summary": str(outcome.get("summary") or "").strip(),
                    "confidence": outcome.get("confidence"),
                    "findings": list(outcome.get("findings") or []),
                    "hypotheses": list(outcome.get("hypotheses") or []),
                    "tools_used": list(outcome.get("tools_used") or []),
                }
            )
        return history[-4:]

    async def _combined_findings(
        self,
        incident_id: str,
        *,
        current_findings: Any,
    ) -> list[str]:
        combined: list[str] = []
        for entry in await self._collaboration_history(incident_id):
            for raw in entry.get("findings") or []:
                finding = str(raw).strip()
                if finding and finding not in combined:
                    combined.append(finding)
        for raw in current_findings:
            finding = str(raw).strip()
            if finding and finding not in combined:
                combined.append(finding)
        return combined

    @staticmethod
    def _receipt_root_cause(specialist_receipt: Any) -> str:
        root_cause = str(getattr(specialist_receipt, "root_cause", None) or "").strip()
        if root_cause:
            return root_cause
        for raw in getattr(specialist_receipt, "hypotheses", ()) or ():
            candidate = str(raw or "").strip()
            if candidate.lower() not in {"", "none", "null", "unknown", "unconfirmed", "n/a"}:
                return candidate
        return "Unconfirmed causal mechanism after bounded autonomous investigation"

    async def _persist_diagnosis_fallback(
        self,
        incident_id: str,
        *,
        specialist_receipt: Any,
        task_id: str,
        validation_status: str,
        validation_summary: str,
        event_type: str,
        event_action: str,
        reason: str,
    ) -> None:
        findings = await self._combined_findings(
            incident_id,
            current_findings=getattr(specialist_receipt, "findings", ()) or (),
        )
        root_cause = self._receipt_root_cause(specialist_receipt)
        summary = str(getattr(specialist_receipt, "summary", None) or "").strip()
        if not summary:
            summary = "Bounded autonomous diagnosis completed with retained specialist evidence."
        confidence = float(getattr(specialist_receipt, "confidence", 0.0) or 0.0)
        next_steps = [
            str(item).strip()
            for item in getattr(specialist_receipt, "recommended_next_steps", ()) or ()
            if str(item).strip()
        ]

        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": "RESOLVED",
                "diagnosis": {
                    "summary": summary,
                    "root_cause": root_cause,
                    "confidence": min(max(confidence, 0.0), 1.0),
                    "evidence": findings,
                },
                "remediation": {
                    "summary": (
                        "No autonomous remediation was executed. The specialist's retained "
                        "recommendations remain advisory."
                    ),
                    "steps": next_steps,
                    "status": "ADVISORY",
                },
                "validation": {
                    "status": validation_status,
                    "summary": validation_summary,
                },
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": [],
                    "review_state": "FALLBACK_COMPLETED",
                    "review_decision": "resolve",
                    "peer_collaboration_state": "COMPLETED",
                },
            },
        )
        if updated is None:
            raise RuntimeError(f"Incident disappeared while persisting diagnosis fallback: {incident_id}")

        await self.repository.add_event(
            incident_id,
            {
                "event_type": event_type,
                "agent_role": "technical_lead",
                "action": event_action,
                "reason": reason,
                "status": "RESOLVED",
                "task_id": task_id,
                "outcome": {
                    "decision": "resolve",
                    "diagnosis_summary": summary,
                    "root_cause": root_cause,
                    "fallback": True,
                },
            },
        )
        self._release_agents(incident_id)

    async def _persist_technical_lead_review_failure(
        self,
        incident_id: str,
        *,
        specialist_receipt: Any,
        task_id: str,
        error: Exception,
    ) -> None:
        self.technical_lead_reviews_failed_count += 1
        LOGGER.exception(
            "Technical Lead review failed; preserving specialist diagnosis instead of operator "
            "escalation: incident=%s error=%s",
            incident_id,
            error,
        )
        await self._persist_diagnosis_fallback(
            incident_id,
            specialist_receipt=specialist_receipt,
            task_id=task_id,
            validation_status="TECHNICAL_LEAD_REVIEW_FALLBACK",
            validation_summary=(
                "The Technical Lead critic cycle failed, but the specialist's bounded diagnosis "
                "and retained evidence were preserved as the incident result."
            ),
            event_type="TECHNICAL_LEAD_REVIEW_FALLBACK",
            event_action="preserve_specialist_diagnosis",
            reason=str(error),
        )

    async def _persist_terminal_diagnostic_failure(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        incident_id = str(incident["incident_id"])
        task_id = str(task["task_id"])
        summary = (
            "The autonomous specialist task failed before a deeper anomaly root cause could be "
            "established. The failure itself is retained as the diagnostic conclusion rather than "
            "changing the incident to OPERATOR_ACTION_REQUIRED."
        )
        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": "RESOLVED",
                "diagnosis": {
                    "summary": summary,
                    "root_cause": f"Autonomous diagnostic execution failure: {reason}",
                    "confidence": 0.0,
                    "evidence": [],
                },
                "remediation": {
                    "summary": "No autonomous remediation was executed.",
                    "steps": [],
                    "status": "ADVISORY",
                },
                "validation": {
                    "status": "DIAGNOSTIC_EXECUTION_FAILED",
                    "summary": summary,
                },
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": [],
                    "review_state": "DIAGNOSTIC_FAILURE_RECORDED",
                    "review_decision": "resolve",
                    "peer_collaboration_state": "COMPLETED",
                },
            },
        )
        if updated is None:
            raise RuntimeError(f"Incident disappeared while persisting task failure: {incident_id}")
        await self.repository.add_event(
            incident_id,
            {
                "event_type": "AUTONOMOUS_DIAGNOSIS_FAILED",
                "agent_role": str(task.get("assigned_to") or "specialist"),
                "action": "persist_diagnostic_failure",
                "reason": reason,
                "status": "RESOLVED",
                "task_id": task_id,
            },
        )
        self._release_agents(incident_id)
        return dict(updated)

    def _release_agents(self, incident_id: str) -> None:
        """Every agent goes idle: the review that just ran is terminal."""

        agents = getattr(self.assignee, "agents", None)
        if not isinstance(agents, list):
            return
        for agent in agents:
            if getattr(agent, "activity_incident_id", None) != incident_id:
                continue
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
        involved = await self._involved_specialist_roles(incident_id)
        collaborative = str(incident.get("status") or "").upper() == "UNDER_ANALYSIS"
        await self.repository.update_incident(
            incident_id,
            {
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": ["technical_lead", *involved] if collaborative else ["technical_lead"],
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
        involved = await self._involved_specialist_roles(incident_id)
        collaborative = str(incident.get("status") or "").upper() == "UNDER_ANALYSIS"
        await self.repository.update_incident(
            incident_id,
            {
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": ["technical_lead", *involved] if collaborative else ["technical_lead"],
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
