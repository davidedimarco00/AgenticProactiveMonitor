"""Autonomous specialist-to-specialist collaboration.

A specialist that finishes its own investigation without a confirmed root cause
may consult exactly one peer in a different domain. The exchange is a direct
XMPP request/response between the two specialists: the Technical Lead does not
authorize it and does not relay any evidence. The requesting specialist folds
the peer's evidence into a single combined result and only that result reaches
the Technical Lead for the final review.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from spade.behaviour import CyclicBehaviour

from .messages import AgentMessage, Performative, parse_spade_message


LOGGER = logging.getLogger("agentic_system.agents.collaboration")

PEER_HELP_REQUEST_TYPE = "peer_help_request"
PEER_HELP_RESPONSE_TYPE = "peer_help_response"


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"Peer collaboration message requires {field}")
    return value


class PeerHelpRequestBehaviour(CyclicBehaviour):
    """Answer a peer's direct request by running an ephemeral ReAct investigation."""

    async def run(self) -> None:
        message = await self.receive(timeout=1)
        if message is None:
            return

        request: AgentMessage | None = None
        try:
            request = parse_spade_message(message)
            self.agent.mark_message_received()
            payload = request.payload

            incident_id = _required_text(payload, "incident_id")
            requester_role = _required_text(payload, "requester_role").lower()
            reason = str(payload.get("reason") or "").strip()
            hop = int(payload.get("hop") or 0)
            if hop >= 1:
                raise RuntimeError(
                    "Peer help is bounded to one consultation; a consulted peer "
                    "cannot itself request further help"
                )
            if requester_role == self.agent.role:
                raise RuntimeError("A specialist cannot request peer help from itself")

            requester_result = payload.get("requester_result") or {}
            if not isinstance(requester_result, dict):
                raise ValueError("requester_result must be a JSON object")

            anomaly = dict(payload.get("anomaly") or {})
            anomaly["peer_collaboration_context"] = {
                "peer_role": requester_role,
                "reason": reason,
                "specialist_result": dict(requester_result),
                "instruction": (
                    "A peer specialist could not confirm the root cause and asked you to "
                    "investigate this same incident from your domain. Treat their findings "
                    "as shared evidence to correlate with your own MCP/RAG observations; do "
                    "not assume their hypotheses are facts."
                ),
            }

            executor = getattr(self.agent, "_react_executor", None)
            if executor is None:
                raise RuntimeError("Specialist ReAct executor was not initialized")

            self.agent.peer_help_requests_served += 1
            self.agent.set_activity(
                "WORKING",
                incident_id=incident_id,
                detail=f"peer_help_for_{requester_role}",
            )

            help_id = f"peer-help:{incident_id}:{uuid4().hex}"
            await self._deliberate(help_id, incident_id, requester_role)

            result = await executor.investigate(
                task_id=help_id,
                incident_id=incident_id,
                agent_role=self.agent.role,
                severity=str(payload.get("severity") or "MEDIUM"),
                entity=str(payload.get("entity") or "unknown"),
                anomaly=anomaly,
            )

            response = AgentMessage.create(
                type=PEER_HELP_RESPONSE_TYPE,
                sender=str(self.agent.jid),
                receiver=request.sender,
                correlation_id=request.correlation_id,
                payload=result.to_payload(),
            )
            await self.agent.send_agent_message(response, performative=Performative.INFORM)
            self.agent.set_activity(
                "WAITING",
                incident_id=incident_id,
                detail="peer_help_delivered",
            )
            LOGGER.warning(
                "%s answered peer help for %s incident=%s confidence=%.3f steps=%d",
                self.agent.display_name,
                requester_role,
                incident_id,
                result.confidence,
                result.react_steps,
            )
        except Exception as exc:
            LOGGER.exception(
                "%s could not answer a peer help request but remains operational: %s",
                self.agent.display_name,
                exc,
            )
            if request is not None:
                failure = AgentMessage.create(
                    type=PEER_HELP_RESPONSE_TYPE,
                    sender=str(self.agent.jid),
                    receiver=request.sender,
                    correlation_id=request.correlation_id,
                    payload={"error": str(exc), "responder_role": self.agent.role},
                )
                try:
                    await self.agent.send_agent_message(
                        failure, performative=Performative.FAILURE
                    )
                except Exception:
                    LOGGER.exception("%s could not report peer help failure", self.agent.display_name)

    async def _deliberate(self, help_id: str, incident_id: str, requester_role: str) -> None:
        runtime = getattr(self.agent, "_bdi_runtime", None)
        deliberate = getattr(runtime, "deliberate_peer_help", None)
        if not callable(deliberate):
            return
        try:
            deliberation = await deliberate(
                help_id=help_id,
                incident_id=incident_id,
                role=self.agent.role,
                peer_role=requester_role,
            )
            self.agent.record_trace(
                {
                    "action": "bdi_intention",
                    "reason": (
                        f"AgentSpeak accepted goal {deliberation.goal} and committed "
                        f"intention {deliberation.investigation_intention} to help {requester_role}."
                    ),
                    "incident_id": incident_id,
                    "task_id": help_id,
                    "outcome": f"peer_help_for={requester_role}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - deliberation trace is best-effort
            LOGGER.warning(
                "%s peer-help BDI deliberation failed, continuing operationally: %s",
                self.agent.display_name,
                exc,
            )


class PeerHelpResponseBehaviour(CyclicBehaviour):
    """Resolve a pending peer help request with the responder's investigation."""

    async def run(self) -> None:
        message = await self.receive(timeout=1)
        if message is None:
            return
        try:
            envelope = parse_spade_message(message)
        except (ValueError, TypeError) as exc:
            LOGGER.warning("%s received an invalid peer help response: %s", self.agent.display_name, exc)
            return

        self.agent.mark_message_received()
        future = self.agent._pending_peer_help.get(envelope.correlation_id)
        if future is None or future.done():
            return

        performative = str(message.get_metadata("performative") or "").upper()
        if performative == Performative.FAILURE.value:
            future.set_exception(
                RuntimeError(str(envelope.payload.get("error") or "Peer help failed"))
            )
        else:
            future.set_result(dict(envelope.payload))


async def request_peer_help(
    agent: Any,
    *,
    receiver: str,
    incident_id: str,
    requester_role: str,
    reason: str,
    requester_result: dict[str, Any],
    severity: str,
    entity: str,
    anomaly: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Send one direct peer help request and await the peer's investigation."""

    if timeout <= 0:
        raise ValueError("Peer help timeout must be greater than zero")

    envelope = AgentMessage.create(
        type=PEER_HELP_REQUEST_TYPE,
        sender=str(agent.jid),
        receiver=receiver,
        payload={
            "incident_id": incident_id,
            "requester_role": requester_role,
            "reason": reason,
            "requester_result": dict(requester_result),
            "severity": severity,
            "entity": entity,
            "anomaly": dict(anomaly),
            "hop": 0,
        },
    )
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    agent._pending_peer_help[envelope.correlation_id] = future
    try:
        await agent.send_agent_message(envelope, performative=Performative.REQUEST)
        return await asyncio.wait_for(future, timeout=timeout)
    finally:
        agent._pending_peer_help.pop(envelope.correlation_id, None)
