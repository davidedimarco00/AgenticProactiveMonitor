from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Awaitable, Callable

import agentspeak
import agentspeak.runtime
import agentspeak.stdlib


_ALLOWED_DECISIONS = {"resolve", "operator_action_required", "request_support"}


@dataclass(frozen=True, slots=True)
class BDIReviewAssessment:
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[str, ...]
    support_domain: str | None = None
    support_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BDIReviewResult:
    incident_id: str
    goal: str
    review_intention: str
    decision_intention: str
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[str, ...]
    support_domain: str | None
    support_reason: str | None


ReviewCallback = Callable[[], Awaitable[BDIReviewAssessment]]


class TechnicalLeadReviewBDIRuntime:
    """AgentSpeak bridge for the Technical Lead post-investigation critic cycle."""

    def __init__(self, *, technical_lead_asl: str, action_timeout_seconds: float = 120.0) -> None:
        if action_timeout_seconds <= 0:
            raise ValueError("action_timeout_seconds must be greater than zero")
        triage_path = Path(technical_lead_asl)
        review_path = triage_path.with_name("technical_lead_review.asl")
        if not review_path.is_file():
            raise RuntimeError(f"Technical Lead review AgentSpeak source not found: {review_path}")
        self._plan = review_path.read_text(encoding="utf-8")
        self.action_timeout_seconds = action_timeout_seconds
        self._semaphore = asyncio.Semaphore(1)

    async def review_specialist_result(
        self,
        *,
        incident_id: str,
        review_callback: ReviewCallback,
    ) -> BDIReviewResult:
        normalized = incident_id.strip()
        if not normalized:
            raise ValueError("Technical Lead review BDI requires incident_id")
        event_loop = asyncio.get_running_loop()
        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_review,
                normalized,
                event_loop,
                review_callback,
            )

    def _run_review(
        self,
        incident_id: str,
        event_loop: asyncio.AbstractEventLoop,
        review_callback: ReviewCallback,
    ) -> BDIReviewResult:
        state: dict[str, object] = {
            "review_intention": None,
            "decision_intention": None,
            "assessment": None,
            "decision": None,
        }
        actions = agentspeak.Actions(agentspeak.stdlib.actions)

        @actions.add_procedure(
            ".run_tl_review",
            (agentspeak.runtime.Agent, agentspeak.asl_str),
        )
        def run_tl_review(agent: agentspeak.runtime.Agent, action_incident_id: str) -> None:
            if action_incident_id != incident_id:
                raise RuntimeError("AgentSpeak review action received a different incident_id")
            state["review_intention"] = "review_specialist_result"
            future = asyncio.run_coroutine_threadsafe(review_callback(), event_loop)
            try:
                assessment = future.result(timeout=self.action_timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                raise RuntimeError("Technical Lead review reasoning timed out") from None
            if assessment.decision not in _ALLOWED_DECISIONS:
                raise RuntimeError(f"Unsupported Technical Lead review decision: {assessment.decision}")
            state["assessment"] = assessment
            agent.add_belief(agentspeak.Literal("review_complete", (incident_id,)), {})
            agent.add_belief(
                agentspeak.Literal(
                    "review_decision",
                    (incident_id, agentspeak.Literal(assessment.decision)),
                ),
                {},
            )

        @actions.add_procedure(
            ".commit_tl_review_decision",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_tl_review_decision(action_incident_id: str, decision: str) -> None:
            if action_incident_id != incident_id:
                raise RuntimeError("AgentSpeak review commit received a different incident_id")
            if decision not in _ALLOWED_DECISIONS:
                raise RuntimeError(f"AgentSpeak committed invalid review decision: {decision}")
            state["decision_intention"] = "commit_review_decision"
            state["decision"] = decision

        source = agentspeak.StringSource(
            "technical_lead_review_runtime.asl",
            "\n".join(
                [
                    f"incident({json.dumps(incident_id)}).",
                    f"incident_status({json.dumps(incident_id)}, under_analysis).",
                    f"specialist_result_received({json.dumps(incident_id)}).",
                    f"!review_investigation_result({json.dumps(incident_id)}).",
                    "",
                    self._plan,
                ]
            ),
        )
        environment = agentspeak.runtime.Environment()
        agent = environment.build_agent(source, actions, name="technical_lead_review_bdi")
        environment.run_agent(agent)

        assessment = state["assessment"]
        if not isinstance(assessment, BDIReviewAssessment):
            raise RuntimeError("AgentSpeak did not execute Technical Lead review reasoning")
        if state["review_intention"] != "review_specialist_result":
            raise RuntimeError("AgentSpeak did not commit to review_specialist_result")
        if state["decision_intention"] != "commit_review_decision":
            raise RuntimeError("AgentSpeak did not commit the review decision")
        if state["decision"] != assessment.decision:
            raise RuntimeError("AgentSpeak review decision differs from the critic assessment")

        return BDIReviewResult(
            incident_id=incident_id,
            goal="review_investigation",
            review_intention="review_specialist_result",
            decision_intention="commit_review_decision",
            decision=assessment.decision,
            confidence=assessment.confidence,
            diagnosis_summary=assessment.diagnosis_summary,
            root_cause=assessment.root_cause,
            rationale=assessment.rationale,
            remediation_summary=assessment.remediation_summary,
            remediation_steps=assessment.remediation_steps,
            support_domain=assessment.support_domain,
            support_reason=assessment.support_reason,
        )
