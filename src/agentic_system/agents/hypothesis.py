from __future__ import annotations

from spade.behaviour import CyclicBehaviour
from spade.template import Template

from ..models import Hypothesis
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class HypothesisAgent(WorkspaceAgent):
    coordinator_jid: str

    class GenerateBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None:
                return
            payload = parse_payload(message)
            incident_id = str(payload["incident_id"])
            incident = await self.agent.workspace.get(incident_id)
            evidence_ids = [item.evidence_id for item in incident.evidence]
            hypotheses = [
                Hypothesis(
                    incident_id=incident_id,
                    statement=f"The anomaly originates on {incident.host_id}",
                    suspected_component=incident.host_id,
                    supporting_evidence_ids=evidence_ids,
                    confidence=0.55,
                ),
                Hypothesis(
                    incident_id=incident_id,
                    statement=f"The anomaly on {incident.host_id} is a secondary symptom caused by a dependency",
                    supporting_evidence_ids=evidence_ids,
                    confidence=0.45,
                ),
            ]
            await self.agent.workspace.replace_hypotheses(incident_id, hypotheses)
            response = build_message(
                self.agent.coordinator_jid,
                MessageType.HYPOTHESES_SUBMITTED,
                {"incident_id": incident_id, "hypotheses": [h.model_dump(mode="json") for h in hypotheses]},
            )
            await self.send(response)

    async def setup(self) -> None:
        template = Template()
        template.set_metadata("message_type", MessageType.HYPOTHESIS_REQUEST.value)
        self.add_behaviour(self.GenerateBehaviour(), template)
