from __future__ import annotations

from spade.behaviour import CyclicBehaviour
from spade.template import Template

from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class CriticAgent(WorkspaceAgent):
    coordinator_jid: str
    acceptance_threshold: float = 0.80

    class CritiqueBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None:
                return
            payload = parse_payload(message)
            incident_id = str(payload["incident_id"])
            incident = await self.agent.workspace.get(incident_id)
            ranked = sorted(incident.hypotheses, key=lambda item: item.confidence, reverse=True)
            best = ranked[0] if ranked else None
            accepted = bool(best and best.confidence >= self.agent.acceptance_threshold)
            response = build_message(
                self.agent.coordinator_jid,
                MessageType.CRITIQUE_SUBMITTED,
                {
                    "incident_id": incident_id,
                    "accepted": accepted,
                    "hypothesis_id": best.hypothesis_id if accepted and best else None,
                    "reason": "confidence threshold reached" if accepted else "additional evidence required",
                },
            )
            await self.send(response)

    async def setup(self) -> None:
        template = Template()
        template.set_metadata("message_type", MessageType.CRITIQUE_REQUEST.value)
        self.add_behaviour(self.CritiqueBehaviour(), template)
