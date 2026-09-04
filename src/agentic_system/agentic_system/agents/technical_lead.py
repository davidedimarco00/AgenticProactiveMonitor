from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Literal

from spade.behaviour import CyclicBehaviour
from spade.template import Template
from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from ..reasoning import AgentSpeakBDIRuntime, BDIReviewAssessment, BDITriageAssessment
from .base import BaseAgent
from .commands import IncidentAssignment
from .messages import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    parse_spade_message,
)
from .prompts import TECHNICAL_LEAD_SYSTEM_PROMPT
from .review import TechnicalLeadReviewDecision, TechnicalLeadReviewReasoner
from .triage import TechnicalLeadTriageDecision, TechnicalLeadTriageReasoner


LOGGER = logging.getLogger("agentic_system.agents.technical_lead")
CONTROL_INBOX_MAXSIZE = 64
REVIEW_BDI_MAX_CYCLES = 3
REVIEW_BDI_RETRY_DELAY_SECONDS = 2.0
ControlKind = Literal["assign", "triage", "review"]


@dataclass(slots=True)
class _ControlCommand:
    """One internal command handled by the Technical Lead control loop."""

    kind: ControlKind
    payload: dict[str, Any]
    completed: asyncio.Future[Any]


class TechnicalLeadAgent(BaseAgent):
    """SPADE Technical Lead with one serialized BDI control loop."""

    SYSTEM_PROMPT = TECHNICAL_LEAD_SYSTEM_PROMPT

    class AcknowledgementBehaviour(CyclicBehaviour):
        """Receive specialist AGREE/REFUSE/FAILURE messages over XMPP."""

        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            try:
                envelope = parse_spade_message(message)
            except (ValueError, TypeError) as exc:
                LOGGER.warning("Technical Lead received invalid specialist response: %s", exc)
                return

            self.agent.mark_message_received()
            self.agent.last_acknowledgement = envelope
            performative = str(message.get_metadata("performative") or "").upper()
            pending = self.agent._pending_acknowledgements.get(envelope.correlation_id)
            if pending is not None and not pending.done():
                if performative == Performative.AGREE.value:
                    pending.set_result(envelope)
                else:
                    error = str(envelope.payload.get("error") or envelope.type)
                    pending.set_exception(
                        RuntimeError(
                            f"Specialist returned {performative or 'ERROR'}: {error}"
                        )
                    )

            LOGGER.info(
                "Technical Lead received %s/%s from %s correlation_id=%s",
                performative or "UNKNOWN",
                envelope.type,
                envelope.sender,
                envelope.correlation_id,
            )

    class ControlBehaviour(CyclicBehaviour):
        """Run take-charge, triage and review commands through one control loop."""

        async def run(self) -> None:
            try:
                command = await asyncio.wait_for(
                    self.agent._control_inbox.get(),
                    timeout=1.0,
                )
            except TimeoutError:
                return

            try:
                result = await self.agent._execute_control(command)
                if not command.completed.done():
                    command.completed.set_result(result)
            except Exception as exc:
                self.agent._record_control_error(command, exc)
                if not command.completed.done():
                    command.completed.set_exception(exc)
            finally:
                self.agent._control_inbox.task_done()

    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
        *,
        provider: LLMProvider,
        mcp_servers: list[MCPServerConfig],
        interaction_memory_path: str,
        agentspeak_technical_lead_asl: str,
        agentspeak_action_timeout_seconds: float,
        agentspeak_bdi_max_concurrency: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            role="technical_lead",
            display_name=display_name,
            health_port=health_port,
            provider=provider,
            mcp_servers=mcp_servers,
            system_prompt=self.SYSTEM_PROMPT,
            interaction_memory_path=interaction_memory_path,
        )
        self._pending_acknowledgements: dict[str, asyncio.Future[AgentMessage]] = {}
        self._control_inbox: asyncio.Queue[_ControlCommand] = asyncio.Queue(
            maxsize=CONTROL_INBOX_MAXSIZE
        )
        self._triage_reasoner = TechnicalLeadTriageReasoner(provider)
        self._review_reasoner = TechnicalLeadReviewReasoner(provider)
        self._bdi_runtime = AgentSpeakBDIRuntime(
            technical_lead_asl=agentspeak_technical_lead_asl,
            action_timeout_seconds=agentspeak_action_timeout_seconds,
            max_concurrency=agentspeak_bdi_max_concurrency,
        )

        self.incidents_received = 0
        self.triages_completed = 0
        self.reviews_completed = 0
        self.last_acknowledgement: AgentMessage | None = None
        self.last_incident_id: str | None = None
        self.last_incident_assignment: IncidentAssignment | None = None
        self.last_assignment_error: str | None = None
        self.last_triage_decision: TechnicalLeadTriageDecision | None = None
        self.last_triage_error: str | None = None
        self.last_review_decision: TechnicalLeadReviewDecision | None = None
        self.last_review_error: str | None = None

    async def setup(self) -> None:
        await super().setup()
        for performative in (
            Performative.AGREE,
            Performative.REFUSE,
            Performative.FAILURE,
        ):
            template = Template()
            template.set_metadata("protocol", AGENTIC_PROTOCOL)
            template.set_metadata("performative", performative.value)
            self.add_behaviour(self.AcknowledgementBehaviour(), template)
        self.add_behaviour(self.ControlBehaviour())

    async def _execute_control(self, command: _ControlCommand) -> Any:
        if command.kind == "assign":
            return await self._take_incident_in_charge(command.payload["assignment"])
        if command.kind == "triage":
            return await self._triage(
                command.payload["incident"],
                detector_context=command.payload["detector_context"],
                available_agents=command.payload["available_agents"],
            )
        if command.kind == "review":
            return await self._review(
                command.payload["incident"],
                specialist_result=command.payload["specialist_result"],
            )
        raise RuntimeError(f"Unsupported Technical Lead control command: {command.kind}")

    async def _take_incident_in_charge(
        self,
        assignment: IncidentAssignment,
    ) -> IncidentAssignment:
        self.set_activity(
            "WORKING",
            incident_id=assignment.incident_id,
            detail="taking_incident_in_charge",
        )
        self.incidents_received += 1
        self.last_incident_id = assignment.incident_id
        self.last_incident_assignment = assignment
        self.last_assignment_error = None
        self.set_activity(
            "WAITING",
            incident_id=assignment.incident_id,
            detail="incident_accepted",
        )
        LOGGER.warning(
            "Technical Lead accepted incident=%s severity=%s entity=%s",
            assignment.incident_id,
            assignment.severity,
            assignment.entity,
        )
        return assignment

    async def _triage(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
        available_agents: list[str],
    ) -> TechnicalLeadTriageDecision:
        assignment = IncidentAssignment.from_incident(incident)
        self.set_activity(
            "WORKING",
            incident_id=assignment.incident_id,
            detail="triaging_incident",
        )
        if assignment.status != "TAKEN_IN_CHARGE":
            raise RuntimeError(
                "Technical Lead triage requires an incident in TAKEN_IN_CHARGE state"
            )

        async def reason_for_triage() -> BDITriageAssessment:
            assessment = await self._triage_reasoner.assess(
                assignment,
                detector_context=detector_context,
                available_agents=available_agents,
            )
            return BDITriageAssessment(
                probable_domain=assessment.probable_domain,
                recommended_agent=assessment.recommended_agent,
                confidence=assessment.confidence,
                rationale=assessment.rationale,
            )

        deliberation = await self._bdi_runtime.triage_incident(
            incident_id=assignment.incident_id,
            anomaly=assignment.anomaly,
            available_agents=available_agents,
            triage_callback=reason_for_triage,
        )
        decision = TechnicalLeadTriageDecision(
            incident_id=assignment.incident_id,
            probable_domain=deliberation.probable_domain,
            primary_investigator=deliberation.primary_investigator,
            confidence=deliberation.confidence,
            rationale=deliberation.rationale,
            bdi_goal=deliberation.goal,
            bdi_triage_intention=deliberation.triage_intention,
            bdi_intention=deliberation.selection_intention,
        )
        self.triages_completed += 1
        self.last_triage_decision = decision
        self.last_triage_error = None
        self.set_activity(
            "WAITING",
            incident_id=assignment.incident_id,
            detail="primary_investigator_selected",
        )
        LOGGER.warning(
            "Technical Lead AgentSpeak triage completed incident=%s goal=%s "
            "triage_intention=%s domain=%s primary=%s selection_intention=%s "
            "confidence=%.3f",
            decision.incident_id,
            decision.bdi_goal,
            decision.bdi_triage_intention,
            decision.probable_domain,
            decision.primary_investigator,
            decision.bdi_intention,
            decision.confidence,
        )
        return decision

    async def _review(
        self,
        incident: dict[str, Any],
        *,
        specialist_result: dict[str, Any],
    ) -> TechnicalLeadReviewDecision:
        incident_id = str(incident.get("incident_id") or "") or None
        if str(incident.get("status") or "").upper() != "UNDER_ANALYSIS":
            raise RuntimeError(
                "Technical Lead review requires an incident in UNDER_ANALYSIS state"
            )

        async def reason_for_review() -> BDIReviewAssessment:
            assessment = await self._review_reasoner.assess(
                incident=incident,
                specialist_result=specialist_result,
            )
            return BDIReviewAssessment(
                decision=assessment.decision,
                confidence=assessment.confidence,
                diagnosis_summary=assessment.diagnosis_summary,
                root_cause=assessment.root_cause,
                rationale=assessment.rationale,
                remediation_summary=assessment.remediation_summary,
                remediation_steps=assessment.remediation_steps,
            )

        deliberation = None
        last_cycle_error: Exception | None = None
        for cycle in range(1, REVIEW_BDI_MAX_CYCLES + 1):
            self.set_activity(
                "WORKING",
                incident_id=incident_id,
                detail="reviewing_specialist_result",
            )
            try:
                deliberation = await self._bdi_runtime.review_specialist_result(
                    incident_id=incident_id or "",
                    review_callback=reason_for_review,
                )
                if cycle > 1:
                    LOGGER.warning(
                        "Technical Lead BDI review recovered after retry cycle: "
                        "incident=%s cycle=%d/%d",
                        incident_id,
                        cycle,
                        REVIEW_BDI_MAX_CYCLES,
                    )
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_cycle_error = exc
                self.last_review_error = str(exc)
                if cycle >= REVIEW_BDI_MAX_CYCLES:
                    raise
                self.set_activity(
                    "WAITING",
                    incident_id=incident_id,
                    detail="review_retrying",
                )
                LOGGER.warning(
                    "Technical Lead BDI review cycle failed transiently; "
                    "keeping incident UNDER_ANALYSIS and retrying without repeating "
                    "specialist work: incident=%s cycle=%d/%d error=%s",
                    incident_id,
                    cycle,
                    REVIEW_BDI_MAX_CYCLES,
                    exc,
                )
                await asyncio.sleep(REVIEW_BDI_RETRY_DELAY_SECONDS)

        if deliberation is None:
            raise last_cycle_error or RuntimeError(
                "Technical Lead BDI review did not produce a deliberation"
            )

        decision = TechnicalLeadReviewDecision(
            incident_id=deliberation.incident_id,
            decision=deliberation.decision,
            confidence=deliberation.confidence,
            diagnosis_summary=deliberation.diagnosis_summary,
            root_cause=deliberation.root_cause,
            rationale=deliberation.rationale,
            remediation_summary=deliberation.remediation_summary,
            remediation_steps=deliberation.remediation_steps,
            bdi_goal=deliberation.goal,
            bdi_review_intention=deliberation.review_intention,
            bdi_decision_intention=deliberation.decision_intention,
        )
        self.reviews_completed += 1
        self.last_review_decision = decision
        self.last_review_error = None
        self.set_activity(
            "WAITING",
            incident_id=incident_id,
            detail="review_decision_committed",
        )
        LOGGER.warning(
            "Technical Lead AgentSpeak review completed incident=%s goal=%s "
            "intention=%s decision=%s confidence=%.3f",
            decision.incident_id,
            decision.bdi_goal,
            decision.bdi_review_intention,
            decision.decision,
            decision.confidence,
        )
        return decision

    def _record_control_error(self, command: _ControlCommand, exc: Exception) -> None:
        incident_id = self._command_incident_id(command)
        if command.kind == "assign":
            self.last_assignment_error = str(exc)
            activity = "IDLE"
            detail = "incident_assignment_failed"
        elif command.kind == "triage":
            self.last_triage_error = str(exc)
            activity = "IDLE"
            detail = "triage_failed"
        else:
            self.last_review_error = str(exc)
            activity = "WAITING"
            detail = "review_failed"

        self.set_activity(activity, incident_id=incident_id, detail=detail)
        LOGGER.exception("Technical Lead %s command failed: %s", command.kind, exc)

    @staticmethod
    def _command_incident_id(command: _ControlCommand) -> str | None:
        if command.kind == "assign":
            assignment = command.payload.get("assignment")
            return str(getattr(assignment, "incident_id", "") or "") or None
        incident = command.payload.get("incident") or {}
        return str(incident.get("incident_id") or "") or None

    async def _submit_control(
        self,
        kind: ControlKind,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        if self.lifecycle_state != "running":
            raise RuntimeError("Technical Lead is not running")

        loop = asyncio.get_running_loop()
        completed: asyncio.Future[Any] = loop.create_future()
        command = _ControlCommand(kind=kind, payload=payload, completed=completed)
        await asyncio.wait_for(self._control_inbox.put(command), timeout=timeout)
        return await asyncio.wait_for(completed, timeout=timeout)

    async def submit_incident(
        self,
        incident: dict[str, Any],
        *,
        timeout: float = 5.0,
    ) -> IncidentAssignment:
        assignment = IncidentAssignment.from_incident(incident)
        return await self._submit_control(
            "assign",
            {"assignment": assignment},
            timeout=timeout,
        )

    async def triage_incident(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
        available_agents: list[str],
        timeout: float = 120.0,
    ) -> TechnicalLeadTriageDecision:
        return await self._submit_control(
            "triage",
            {
                "incident": dict(incident),
                "detector_context": dict(detector_context),
                "available_agents": list(available_agents),
            },
            timeout=timeout,
        )

    async def review_specialist_result(
        self,
        incident: dict[str, Any],
        *,
        specialist_result: dict[str, Any],
        timeout: float = 360.0,
    ) -> TechnicalLeadReviewDecision:
        return await self._submit_control(
            "review",
            {
                "incident": dict(incident),
                "specialist_result": dict(specialist_result),
            },
            timeout=timeout,
        )

    async def request_specialist(
        self,
        *,
        receiver: str,
        request_type: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> tuple[AgentMessage, AgentMessage]:
        request = AgentMessage.create(
            type=request_type,
            sender=str(self.jid),
            receiver=receiver,
            payload=payload,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMessage] = loop.create_future()
        self._pending_acknowledgements[request.correlation_id] = future
        try:
            await self.send_agent_message(request, performative=Performative.REQUEST)
            acknowledgement = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_acknowledgements.pop(request.correlation_id, None)
        return request, acknowledgement

    def _queued_control_count(self, kind: ControlKind) -> int:
        return sum(
            1
            for command in tuple(self._control_inbox._queue)  # noqa: SLF001
            if command.kind == kind
        )

    def health_snapshot(self) -> dict[str, Any]:
        snapshot = super().health_snapshot()
        last_triage = None
        if self.last_triage_decision is not None:
            last_triage = {
                "incident_id": self.last_triage_decision.incident_id,
                "probable_domain": self.last_triage_decision.probable_domain,
                "primary_investigator": self.last_triage_decision.primary_investigator,
                "confidence": self.last_triage_decision.confidence,
                "rationale": self.last_triage_decision.rationale,
                "bdi_goal": self.last_triage_decision.bdi_goal,
                "bdi_triage_intention": self.last_triage_decision.bdi_triage_intention,
                "bdi_intention": self.last_triage_decision.bdi_intention,
            }

        last_review = None
        if self.last_review_decision is not None:
            last_review = {
                "incident_id": self.last_review_decision.incident_id,
                "decision": self.last_review_decision.decision,
                "confidence": self.last_review_decision.confidence,
                "rationale": self.last_review_decision.rationale,
                "bdi_goal": self.last_review_decision.bdi_goal,
                "bdi_review_intention": self.last_review_decision.bdi_review_intention,
                "bdi_decision_intention": self.last_review_decision.bdi_decision_intention,
            }

        snapshot.update(
            {
                "control_inbox_depth": self._control_inbox.qsize(),
                "control_inbox_maxsize": self._control_inbox.maxsize,
                "incident_inbox_depth": self._queued_control_count("assign"),
                "incident_inbox_maxsize": self._control_inbox.maxsize,
                "triage_inbox_depth": self._queued_control_count("triage"),
                "triage_inbox_maxsize": self._control_inbox.maxsize,
                "review_inbox_depth": self._queued_control_count("review"),
                "review_inbox_maxsize": self._control_inbox.maxsize,
                "incidents_received": self.incidents_received,
                "triages_completed": self.triages_completed,
                "reviews_completed": self.reviews_completed,
                "last_incident_id": self.last_incident_id,
                "last_assignment_error": self.last_assignment_error,
                "last_triage": last_triage,
                "last_triage_error": self.last_triage_error,
                "last_review": last_review,
                "last_review_error": self.last_review_error,
            }
        )
        return snapshot
