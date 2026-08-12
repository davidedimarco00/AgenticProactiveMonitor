from __future__ import annotations

from collections import defaultdict

from spade.behaviour import CyclicBehaviour

from ..models import Incident
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class IncidentCoordinatorAgent(WorkspaceAgent):
    evidence_agents: list[str]
    hypothesis_agent: str
    critic_agent: str
    investigation_agent: str
    max_rounds: int = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._evidence_responses: dict[str, set[str]] = defaultdict(set)
        self._pending_tests: dict[str, set[str]] = defaultdict(set)

    async def open_incident(self, incident: Incident) -> None:
        await self.workspace.create(incident)
        await self._start_investigation_round(incident.incident_id)

    async def _start_investigation_round(self, incident_id: str) -> None:
        await self.workspace.start_round(incident_id)
        self._evidence_responses[incident_id].clear()
        for jid in self.evidence_agents:
            await self.send(build_message(jid, MessageType.EVIDENCE_REQUEST, {"incident_id": incident_id}))

    class CoordinationBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None:
                return
            message_type = message.metadata.get("message_type")
            payload = parse_payload(message)
            incident_id = str(payload["incident_id"])

            if message_type == MessageType.EVIDENCE_SUBMITTED.value:
                self.agent._evidence_responses[incident_id].add(str(payload["source_agent"]))
                if len(self.agent._evidence_responses[incident_id]) >= len(self.agent.evidence_agents):
                    await self.send(build_message(self.agent.hypothesis_agent, MessageType.HYPOTHESIS_REQUEST, {"incident_id": incident_id}))

            elif message_type == MessageType.HYPOTHESES_SUBMITTED.value:
                await self.send(build_message(self.agent.critic_agent, MessageType.CRITIQUE_REQUEST, {"incident_id": incident_id}))

            elif message_type == MessageType.CRITIQUE_SUBMITTED.value:
                if payload.get("accepted") and payload.get("hypothesis_id"):
                    await self.agent.workspace.confirm(incident_id, str(payload["hypothesis_id"]))
                    self.agent.log.info("Incident %s diagnosed", incident_id)
                else:
                    incident = await self.agent.workspace.get(incident_id)
                    if incident.investigation_round >= self.agent.max_rounds:
                        await self.agent.workspace.fail(incident_id)
                    else:
                        await self.send(build_message(self.agent.investigation_agent, MessageType.INVESTIGATION_REQUEST, {"incident_id": incident_id}))

            elif message_type == MessageType.TEST_PLAN_SUBMITTED.value:
                self.agent._pending_tests[incident_id] = set(map(str, payload.get("tests", [])))
                if not self.agent._pending_tests[incident_id]:
                    await self.agent._start_investigation_round(incident_id)

            elif message_type == MessageType.DIAGNOSTIC_TEST_COMPLETED.value:
                self.agent._pending_tests[incident_id].discard(str(payload["test_id"]))
                if not self.agent._pending_tests[incident_id]:
                    await self.send(build_message(self.agent.critic_agent, MessageType.CRITIQUE_REQUEST, {"incident_id": incident_id}))

    async def setup(self) -> None:
        self.add_behaviour(self.CoordinationBehaviour())
