from __future__ import annotations

from typing import Any

from .react_coordinator import ReActIncidentCoordinator as _BaseReActIncidentCoordinator


class ReActIncidentCoordinator(_BaseReActIncidentCoordinator):
    """Evidence-aware coordinator persistence for operator-facing final results.

    The base coordinator owns workflow sequencing, durable retries and peer
    collaboration. This specialization only persists the terminal critic result
    with a strict distinction between specialist diagnostic confidence and the
    Technical Lead confidence in its workflow/review decision.

    ``OPERATOR_ACTION_REQUIRED`` is intentionally not a terminal diagnostic
    outcome in the autonomous workflow. Human actions are persisted as advisory
    remediation attached to a resolved diagnosis; they must not replace the
    diagnosis or become a fallback for model/review uncertainty.
    """

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

        # The Technical Lead review is now purely terminal. Peer collaboration is
        # initiated autonomously by the requesting specialist before the result
        # ever reaches the Technical Lead, so neither OPERATOR_ACTION_REQUIRED nor
        # REQUEST_SUPPORT is a valid non-terminal continuation here. Both are
        # normalized to a resolved, evidence-backed diagnosis.
        if decision in {"operator_action_required", "request_support"}:
            decision = "resolve"

        involved_roles = await self._involved_specialist_roles(incident_id)
        evidence = await self._combined_findings(
            incident_id,
            current_findings=specialist_receipt.findings,
        )
        diagnostic_confidence = float(specialist_receipt.confidence)
        review_confidence = float(review.confidence)
        diagnosis = {
            "summary": review.diagnosis_summary,
            "root_cause": review.root_cause,
            "confidence": diagnostic_confidence,
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
                "The autonomous diagnostic workflow completed with the best evidence-backed "
                "diagnosis. Any human corrective actions remain advisory remediation and do "
                "not replace the diagnosis with an operator-escalation state."
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
                    "review_confidence": review_confidence,
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
                    "diagnostic_confidence": diagnostic_confidence,
                    "review_confidence": review_confidence,
                    "diagnosis_summary": review.diagnosis_summary,
                    "specialists_involved": involved_roles,
                },
            },
        )
        self.technical_lead_reviews_completed_count += 1
        self._release_agents(incident_id)

        from .react_coordinator import LOGGER

        LOGGER.warning(
            "Technical Lead BDI review persisted: incident=%s decision=%s status=%s "
            "diagnostic_confidence=%.3f review_confidence=%.3f specialists=%s",
            incident_id,
            decision,
            status,
            diagnostic_confidence,
            review_confidence,
            ",".join(involved_roles) or "none",
        )
