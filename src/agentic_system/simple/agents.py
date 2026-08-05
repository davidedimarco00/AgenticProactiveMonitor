from __future__ import annotations

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour

from .models import CriticReview, DiagnosticCheck, IncidentContext, IncidentStatus
from .protocol import MessageType, body, message
from .services import CriticService, EvidenceService, ReasoningService, RemediationService
from .workspace import Workspace


class BaseAgent(Agent):
    def __init__(self, jid: str, password: str, workspace: Workspace):
        super().__init__(jid, password)
        self.workspace = workspace


class CoordinatorAgent(BaseAgent):
    evidence_jid: str
    reasoning_jid: str
    critic_jid: str
    remediation_jid: str
    max_rounds = 3

    async def open(self, incident: IncidentContext) -> None:
        await self.workspace.create(incident)
        await self.workspace.begin_round(incident.incident_id)
        await self.send(message(self.evidence_jid, MessageType.COLLECT, incident.incident_id, checks=[]))

    class Behaviour(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=10)
            if msg is None:
                return
            data = body(msg)
            incident_id = data["incident_id"]
            kind = msg.metadata.get("message_type")
            if kind == MessageType.EVIDENCE_READY.value:
                await self.workspace.set_status(incident_id, IncidentStatus.REASONING)
                await self.send(message(self.agent.reasoning_jid, MessageType.REASON, incident_id))
            elif kind == MessageType.DIAGNOSIS_READY.value:
                await self.send(message(self.agent.critic_jid, MessageType.REVIEW, incident_id))
            elif kind == MessageType.REVIEW_READY.value:
                review = CriticReview.model_validate(data["review"])
                if review.accepted:
                    await self.workspace.set_status(incident_id, IncidentStatus.REMEDIATING)
                    await self.send(message(self.agent.remediation_jid, MessageType.REMEDIATE, incident_id))
                else:
                    incident = await self.workspace.get(incident_id)
                    if incident.round >= self.agent.max_rounds or not review.required_checks:
                        await self.workspace.set_status(incident_id, IncidentStatus.FAILED)
                    else:
                        await self.workspace.begin_round(incident_id)
                        await self.send(
                            message(
                                self.agent.evidence_jid,
                                MessageType.COLLECT,
                                incident_id,
                                checks=[item.model_dump() for item in review.required_checks],
                            )
                        )
            elif kind == MessageType.REMEDIATION_READY.value:
                report = data["report"]
                await self.workspace.set_remediation(incident_id, report)
                await self.workspace.set_status(
                    incident_id,
                    IncidentStatus.RESOLVED if report.get("executed") else IncidentStatus.FAILED,
                )
            elif kind == MessageType.FAILURE.value:
                await self.workspace.set_status(incident_id, IncidentStatus.FAILED)
                self.agent.log.error(
                    "Incident %s failed in %s: %s",
                    incident_id,
                    data.get("stage"),
                    data.get("error"),
                )

    async def setup(self):
        self.add_behaviour(self.Behaviour())


class EvidenceAgent(BaseAgent):
    coordinator_jid: str
    service: EvidenceService

    class Behaviour(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=10)
            if msg is None or msg.metadata.get("message_type") != MessageType.COLLECT.value:
                return
            data = body(msg)
            incident_id = data["incident_id"]
            try:
                incident = await self.workspace.get(incident_id)
                checks = [DiagnosticCheck.model_validate(item) for item in data.get("checks", [])]
                report = await self.agent.service.collect(incident, checks)
                await self.workspace.add_evidence(incident.incident_id, report)
                await self.send(
                    message(self.agent.coordinator_jid, MessageType.EVIDENCE_READY, incident.incident_id)
                )
            except Exception as exc:
                await self.send(
                    message(
                        self.agent.coordinator_jid,
                        MessageType.FAILURE,
                        incident_id,
                        stage="evidence",
                        error=str(exc),
                    )
                )

    async def setup(self):
        self.add_behaviour(self.Behaviour())


class ReasoningAgent(BaseAgent):
    coordinator_jid: str
    service: ReasoningService

    class Behaviour(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=10)
            if msg is None or msg.metadata.get("message_type") != MessageType.REASON.value:
                return
            incident_id = body(msg)["incident_id"]
            try:
                incident = await self.workspace.get(incident_id)
                diagnosis = await self.agent.service.diagnose(incident)
                await self.workspace.set_diagnosis(incident.incident_id, diagnosis)
                await self.send(
                    message(self.agent.coordinator_jid, MessageType.DIAGNOSIS_READY, incident.incident_id)
                )
            except Exception as exc:
                await self.send(
                    message(
                        self.agent.coordinator_jid,
                        MessageType.FAILURE,
                        incident_id,
                        stage="reasoning",
                        error=str(exc),
                    )
                )

    async def setup(self):
        self.add_behaviour(self.Behaviour())


class CriticAgent(BaseAgent):
    coordinator_jid: str
    service: CriticService

    class Behaviour(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=10)
            if msg is None or msg.metadata.get("message_type") != MessageType.REVIEW.value:
                return
            incident_id = body(msg)["incident_id"]
            try:
                incident = await self.workspace.get(incident_id)
                review = await self.agent.service.review(incident)
                await self.workspace.set_review(incident.incident_id, review)
                await self.send(
                    message(
                        self.agent.coordinator_jid,
                        MessageType.REVIEW_READY,
                        incident.incident_id,
                        review=review.model_dump(),
                    )
                )
            except Exception as exc:
                await self.send(
                    message(
                        self.agent.coordinator_jid,
                        MessageType.FAILURE,
                        incident_id,
                        stage="critic",
                        error=str(exc),
                    )
                )

    async def setup(self):
        self.add_behaviour(self.Behaviour())


class RemediationAgent(BaseAgent):
    coordinator_jid: str
    service: RemediationService

    class Behaviour(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=10)
            if msg is None or msg.metadata.get("message_type") != MessageType.REMEDIATE.value:
                return
            incident_id = body(msg)["incident_id"]
            try:
                incident = await self.workspace.get(incident_id)
                report = await self.agent.service.execute(incident)
                await self.send(
                    message(
                        self.agent.coordinator_jid,
                        MessageType.REMEDIATION_READY,
                        incident.incident_id,
                        report=report,
                    )
                )
            except Exception as exc:
                await self.send(
                    message(
                        self.agent.coordinator_jid,
                        MessageType.FAILURE,
                        incident_id,
                        stage="remediation",
                        error=str(exc),
                    )
                )

    async def setup(self):
        self.add_behaviour(self.Behaviour())
