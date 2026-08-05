from __future__ import annotations

from spade.behaviour import CyclicBehaviour

from ..models import Evidence, EvidenceType
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class TopologyAgent(WorkspaceAgent):
    coordinator_jid: str
    topology: dict[str, list[str]]

    class Behaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None or message.metadata.get("message_type") != MessageType.EVIDENCE_REQUEST.value:
                return
            payload = parse_payload(message)
            incident = await self.agent.workspace.get(str(payload["incident_id"]))
            dependencies = self.agent.topology.get(incident.host_id, [])
            evidence = Evidence(
                incident_id=incident.incident_id,
                source_agent=str(self.agent.jid),
                evidence_type=EvidenceType.TOPOLOGY,
                summary=f"{incident.host_id} depends on {dependencies or ['no registered components']}",
                details={"host": incident.host_id, "dependencies": dependencies},
                confidence=1.0,
            )
            await self.agent.workspace.add_evidence(incident.incident_id, evidence)
            await self.send(build_message(self.agent.coordinator_jid, MessageType.EVIDENCE_SUBMITTED, {
                "incident_id": incident.incident_id,
                "source_agent": str(self.agent.jid),
                "evidence_id": evidence.evidence_id,
            }))

    async def setup(self) -> None:
        self.add_behaviour(self.Behaviour())
