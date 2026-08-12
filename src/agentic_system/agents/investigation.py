from __future__ import annotations

from spade.behaviour import CyclicBehaviour

from ..models import DiagnosticTest
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class InvestigationAgent(WorkspaceAgent):
    coordinator_jid: str
    diagnostic_executor_jid: str

    class Behaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None or message.metadata.get("message_type") != MessageType.INVESTIGATION_REQUEST.value:
                return
            payload = parse_payload(message)
            incident = await self.agent.workspace.get(str(payload["incident_id"]))
            candidates = [h for h in incident.hypotheses if not h.rejected]
            if not candidates:
                await self.send(build_message(self.agent.coordinator_jid, MessageType.TEST_PLAN_SUBMITTED, {
                    "incident_id": incident.incident_id, "tests": []
                }))
                return
            hypothesis = max(candidates, key=lambda h: h.confidence)
            tests = [
                DiagnosticTest(
                    incident_id=incident.incident_id,
                    hypothesis_id=hypothesis.hypothesis_id,
                    action="inspect_container",
                    target=hypothesis.suspected_component or incident.host_id,
                    rationale="Inspect runtime state and health without changing the system",
                ),
                DiagnosticTest(
                    incident_id=incident.incident_id,
                    hypothesis_id=hypothesis.hypothesis_id,
                    action="get_container_stats",
                    target=hypothesis.suspected_component or incident.host_id,
                    rationale="Collect CPU and memory evidence to discriminate the hypothesis",
                ),
            ]
            for test in tests:
                incident.diagnostic_tests.append(test)
                await self.send(build_message(self.agent.diagnostic_executor_jid, MessageType.DIAGNOSTIC_TEST_REQUEST, test.model_dump(mode="json")))
            await self.send(build_message(self.agent.coordinator_jid, MessageType.TEST_PLAN_SUBMITTED, {
                "incident_id": incident.incident_id,
                "tests": [test.test_id for test in tests],
            }))

    async def setup(self) -> None:
        self.add_behaviour(self.Behaviour())
