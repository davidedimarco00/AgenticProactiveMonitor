from __future__ import annotations

from abc import ABC, abstractmethod

from spade.behaviour import CyclicBehaviour
from spade.template import Template

from ..models import Evidence
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class EvidenceAgent(WorkspaceAgent, ABC):
    coordinator_jid: str

    @abstractmethod
    async def collect_evidence(self, incident_id: str) -> list[Evidence]:
        raise NotImplementedError

    class RequestBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None:
                return
            payload = parse_payload(message)
            incident_id = str(payload["incident_id"])
            evidence = await self.agent.collect_evidence(incident_id)
            for item in evidence:
                await self.agent.workspace.add_evidence(item)
            response = build_message(
                self.agent.coordinator_jid,
                MessageType.EVIDENCE_SUBMITTED,
                {
                    "incident_id": incident_id,
                    "source_agent": str(self.agent.jid),
                    "evidence": [item.model_dump(mode="json") for item in evidence],
                },
            )
            await self.send(response)

    async def setup(self) -> None:
        template = Template()
        template.set_metadata("message_type", MessageType.EVIDENCE_REQUEST.value)
        self.add_behaviour(self.RequestBehaviour(), template)


class MetricsAgent(EvidenceAgent):
    async def collect_evidence(self, incident_id: str) -> list[Evidence]:
        incident = await self.workspace.get(incident_id)
        return [
            Evidence(
                incident_id=incident_id,
                source_agent=str(self.jid),
                evidence_type="metric",
                summary=f"Anomaly score {incident.anomaly_score:.2f} observed for {incident.metric_name}",
                details={"host_id": incident.host_id, "metric_name": incident.metric_name},
                confidence=min(1.0, incident.anomaly_score),
            )
        ]


class LogsAgent(EvidenceAgent):
    async def collect_evidence(self, incident_id: str) -> list[Evidence]:
        incident = await self.workspace.get(incident_id)
        return [
            Evidence(
                incident_id=incident_id,
                source_agent=str(self.jid),
                evidence_type="log",
                summary=f"Log analysis requested around anomaly on {incident.host_id}",
                details={"query_status": "repository_not_connected_yet"},
                confidence=0.30,
            )
        ]
