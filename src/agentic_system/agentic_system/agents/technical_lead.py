from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from spade.behaviour import CyclicBehaviour
from spade.template import Template
from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from ..reasoning import AgentSpeakBDIRuntime, BDITriageAssessment
from .base import BaseAgent
from .commands import IncidentAssignment
from .messages import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    parse_spade_message,
)
from .prompts import TECHNICAL_LEAD_SYSTEM_PROMPT
from .triage import TechnicalLeadTriageDecision, TechnicalLeadTriageReasoner


LOGGER = logging.getLogger("agentic_system.agents.technical_lead")
INCIDENT_INBOX_MAXSIZE = 64
TRIAGE_INBOX_MAXSIZE = 32


@dataclass(slots=True)
class _PendingIncidentAssignment:
    assignment: IncidentAssignment
    accepted: asyncio.Future[None]


@dataclass(slots=True)
class _PendingTriage:
    incident: dict[str, Any]
    detector_context: dict[str, Any]
    available_agents: list[str]
    completed: asyncio.Future[TechnicalLeadTriageDecision]


class TechnicalLeadAgent(BaseAgent):
    SYSTEM_PROMPT = TECHNICAL_LEAD_SYSTEM_PROMPT

    class AcknowledgementBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            try:
                envelope = parse_spade_message(message)
            except (ValueError, TypeError) as exc:
                LOGGER.warning("Technical Lead received invalid AGREE message: %s", exc)
                return

            self.agent.mark_message_received()
            self.agent.last_acknowledgement = envelope

            pending = self.agent._pending_acknowledgements.get(envelope.correlation_id)
            if pending is not None and not pending.done():
                pending.set_result(envelope)

            LOGGER.info(
                "Technical Lead received AGREE/%s from %s correlation_id=%s",
                envelope.type,
                envelope.sender,
                envelope.correlation_id,
            )

    class IncidentAssignmentBehaviour(CyclicBehaviour):
        """Accept persisted incidents from the application ingress queue."""

        async def run(self) -> None:
            try:
                pending = await asyncio.wait_for(
                    self.agent._incident_inbox.get(),
                    timeout=1.0,
                )
            except TimeoutError:
                return

            try:
                assignment = pending.assignment
                self.agent.set_activity(
                    "WORKING",
                    incident_id=assignment.incident_id,
                    detail="taking_incident_in_charge",
                )
                self.agent.incidents_received += 1
                self.agent.last_incident_id = assignment.incident_id
                self.agent.last_incident_assignment = assignment
                if not pending.accepted.done():
                    pending.accepted.set_result(None)

                LOGGER.warning(
                    "Technical Lead accepted incident=%s severity=%s entity=%s",
                    assignment.incident_id,
                    assignment.severity,
                    assignment.entity,
                )
            except Exception as exc:
                if not pending.accepted.done():
                    pending.accepted.set_exception(exc)
                raise
            finally:
                self.agent._incident_inbox.task_done()

    class TriageBehaviour(CyclicBehaviour):
        """Run one BDI-led triage cycle without diagnosing or delegating yet."""

        async def run(self) -> None:
            try:
                pending = await asyncio.wait_for(
                    self.agent._triage_inbox.get(),
                    timeout=1.0,
                )
            except TimeoutError:
                return

            incident_id = str(pending.incident.get("incident_id") or "") or None
            try:
                assignment = IncidentAssignment.from_incident(pending.incident)
                self.agent.set_activity(
                    "WORKING",
                    incident_id=assignment.incident_id,
                    detail="triaging_incident",
                )
                if assignment.status != "TAKEN_IN_CHARGE":
                    raise RuntimeError(
                        "Technical Lead triage requires an incident in TAKEN_IN_CHARGE state"
                    )

                async def reason_for_triage() -> BDITriageAssessment:
                    assessment = await self.agent._triage_reasoner.assess(
                        assignment,
                        detector_context=pending.detector_context,
                        available_agents=pending.available_agents,
                    )
                    return BDITriageAssessment(
                        probable_domain=assessment.probable_domain,
                        recommended_agent=assessment.recommended_agent,
                        confidence=assessment.confidence,
                        rationale=assessment.rationale,
                    )

                deliberation = await self.agent._bdi_runtime.triage_incident(
                    incident_id=assignment.incident_id,
                    anomaly=assignment.anomaly,
                    available_agents=pending.available_agents,
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
                self.agent.triages_completed += 1
                self.agent.last_triage_decision = decision
                self.agent.last_triage_error = None
                self.agent.set_activity(
                    "WAITING",
                    incident_id=assignment.incident_id,
                    detail="primary_investigator_selected",
                )

                if not pending.completed.done():
                    pending.completed.set_result(decision)

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
            except Exception as exc:
                self.agent.last_triage_error = str(exc)
                self.agent.set_activity(
                    "WAITING",
                    incident_id=incident_id,
                    detail="triage_failed",
                )
                if not pending.completed.done():
                    pending.completed.set_exception(exc)
                LOGGER.exception("Technical Lead triage failed: %s", exc)
            finally:
                self.agent._triage_inbox.task_done()

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
        self.last_acknowledgement: AgentMessage | None = None
        self._incident_inbox: asyncio.Queue[_PendingIncidentAssignment] = asyncio.Queue(
            maxsize=INCIDENT_INBOX_MAXSIZE
        )
        self._triage_inbox: asyncio.Queue[_PendingTriage] = asyncio.Queue(
            maxsize=TRIAGE_INBOX_MAXSIZE
        )
        self._triage_reasoner = TechnicalLeadTriageReasoner(provider)
        self._bdi_runtime = AgentSpeakBDIRuntime(
            technical_lead_asl=agentspeak_technical_lead_asl,
            action_timeout_seconds=agentspeak_action_timeout_seconds,
            max_concurrency=agentspeak_bdi_max_concurrency,
        )
        self.incidents_received = 0
        self.triages_completed = 0
        self.last_incident_id: str | None = None
        self.last_incident_assignment: IncidentAssignment | None = None
        self.last_triage_decision: TechnicalLeadTriageDecision | None = None
        self.last_triage_error: str | None = None

    async def setup(self) -> None:
        await super().setup()
        template = Template()
        template.set_metadata("protocol", AGENTIC_PROTOCOL)
        template.set_metadata("performative", Performative.AGREE.value)
        self.add_behaviour(self.AcknowledgementBehaviour(), template)
        self.add_behaviour(self.IncidentAssignmentBehaviour())
        self.add_behaviour(self.TriageBehaviour())

    async def submit_incident(
        self,
        incident: dict[str, Any],
        *,
        timeout: float = 5.0,
    ) -> IncidentAssignment:
        """Deliver a persisted incident to the Technical Lead SPADE behaviour."""

        if self.lifecycle_state != "running":
            raise RuntimeError("Technical Lead is not running")

        assignment = IncidentAssignment.from_incident(incident)
        loop = asyncio.get_running_loop()
        accepted: asyncio.Future[None] = loop.create_future()
        await self._incident_inbox.put(
            _PendingIncidentAssignment(
                assignment=assignment,
                accepted=accepted,
            )
        )
        await asyncio.wait_for(accepted, timeout=timeout)
        return assignment

    async def triage_incident(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
        available_agents: list[str],
        timeout: float = 120.0,
    ) -> TechnicalLeadTriageDecision:
        """Perform BDI-led first analysis without diagnosing or delegating the incident."""

        if self.lifecycle_state != "running":
            raise RuntimeError("Technical Lead is not running")

        loop = asyncio.get_running_loop()
        completed: asyncio.Future[TechnicalLeadTriageDecision] = loop.create_future()
        await self._triage_inbox.put(
            _PendingTriage(
                incident=dict(incident),
                detector_context=dict(detector_context),
                available_agents=list(available_agents),
                completed=completed,
            )
        )
        return await asyncio.wait_for(completed, timeout=timeout)

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
        snapshot.update(
            {
                "incident_inbox_depth": self._incident_inbox.qsize(),
                "incident_inbox_maxsize": self._incident_inbox.maxsize,
                "triage_inbox_depth": self._triage_inbox.qsize(),
                "triage_inbox_maxsize": self._triage_inbox.maxsize,
                "incidents_received": self.incidents_received,
                "triages_completed": self.triages_completed,
                "last_incident_id": self.last_incident_id,
                "last_triage": last_triage,
                "last_triage_error": self.last_triage_error,
            }
        )
        return snapshot
