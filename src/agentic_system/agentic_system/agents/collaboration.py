from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from spade.behaviour import CyclicBehaviour

from .messages import AgentMessage, Performative, parse_spade_message


LOGGER = logging.getLogger("agentic_system.agents.collaboration")
PEER_COLLABORATION_CONTEXT_TYPE = "peer_collaboration_context"
PEER_COLLABORATION_CONTEXT_ACCEPTED_TYPE = "peer_collaboration_context_accepted"
PEER_COLLABORATION_RESULT_TYPE = "peer_collaboration_result"


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"Peer collaboration message requires {field}")
    return value


def normalize_peer_context(payload: dict[str, Any], *, sender: str) -> dict[str, Any]:
    incident_id = _required_text(payload, "incident_id")
    support_task_id = _required_text(payload, "support_task_id")
    peer_role = _required_text(payload, "peer_role").lower()
    target_role = _required_text(payload, "target_role").lower()
    authorized_by = _required_text(payload, "authorized_by").lower()
    if authorized_by != "technical_lead":
        raise ValueError("Peer collaboration must be authorized by the Technical Lead")

    result = payload.get("specialist_result") or {}
    if not isinstance(result, dict):
        raise ValueError("specialist_result must be a JSON object")

    return {
        "incident_id": incident_id,
        "support_task_id": support_task_id,
        "peer_role": peer_role,
        "peer_jid": sender,
        "target_role": target_role,
        "authorized_by": authorized_by,
        "support_domain": str(payload.get("support_domain") or "").strip().lower() or None,
        "support_reason": str(payload.get("support_reason") or "").strip() or None,
        "specialist_result": dict(result),
    }


class PeerCollaborationContextBehaviour(CyclicBehaviour):
    """Receive Technical-Lead-authorized evidence directly from another specialist."""

    async def run(self) -> None:
        message = await self.receive(timeout=1)
        if message is None:
            return

        request: AgentMessage | None = None
        try:
            request = parse_spade_message(message)
            self.agent.mark_message_received()
            context = normalize_peer_context(request.payload, sender=request.sender)
            if context["target_role"] != self.agent.role:
                raise RuntimeError(
                    f"Peer collaboration targets {context['target_role']}, not {self.agent.role}"
                )
            if context["peer_role"] == self.agent.role:
                raise RuntimeError("A specialist cannot open peer collaboration with itself")

            self.agent._peer_context_by_task[context["support_task_id"]] = context
            self.agent.peer_contexts_received += 1
            self.agent.last_peer_context = context
            self.agent.set_activity(
                "WAITING",
                incident_id=context["incident_id"],
                detail="peer_context_received",
            )

            acknowledgement = AgentMessage.create(
                type=PEER_COLLABORATION_CONTEXT_ACCEPTED_TYPE,
                sender=str(self.agent.jid),
                receiver=request.sender,
                correlation_id=request.correlation_id,
                payload={
                    "incident_id": context["incident_id"],
                    "support_task_id": context["support_task_id"],
                    "accepted_by": self.agent.role,
                    "peer_role": context["peer_role"],
                },
            )
            await self.agent.send_agent_message(
                acknowledgement,
                performative=Performative.AGREE,
            )
            LOGGER.warning(
                "%s accepted direct peer context from %s for incident=%s support_task=%s",
                self.agent.display_name,
                context["peer_role"],
                context["incident_id"],
                context["support_task_id"],
            )
        except Exception as exc:
            LOGGER.exception(
                "%s could not accept peer collaboration context: %s",
                self.agent.display_name,
                exc,
            )
            if request is not None:
                failure = AgentMessage.create(
                    type=PEER_COLLABORATION_CONTEXT_ACCEPTED_TYPE,
                    sender=str(self.agent.jid),
                    receiver=request.sender,
                    correlation_id=request.correlation_id,
                    payload={"accepted_by": self.agent.role, "error": str(exc)},
                )
                try:
                    await self.agent.send_agent_message(
                        failure,
                        performative=Performative.FAILURE,
                    )
                except Exception:
                    LOGGER.exception("Could not report peer-context refusal")


class PeerCollaborationAcknowledgementBehaviour(CyclicBehaviour):
    """Resolve a pending specialist-to-specialist context handshake."""

    async def run(self) -> None:
        message = await self.receive(timeout=1)
        if message is None:
            return
        try:
            envelope = parse_spade_message(message)
        except (ValueError, TypeError) as exc:
            LOGGER.warning("Invalid peer collaboration acknowledgement: %s", exc)
            return

        self.agent.mark_message_received()
        future = self.agent._pending_peer_acknowledgements.get(envelope.correlation_id)
        if future is not None and not future.done():
            performative = str(message.get_metadata("performative") or "").upper()
            if performative == Performative.FAILURE.value:
                future.set_exception(
                    RuntimeError(str(envelope.payload.get("error") or "Peer refused collaboration"))
                )
            else:
                future.set_result(envelope)


class PeerCollaborationResultBehaviour(CyclicBehaviour):
    """Receive a support specialist result directly, without routing it through the TL."""

    async def run(self) -> None:
        message = await self.receive(timeout=1)
        if message is None:
            return
        try:
            envelope = parse_spade_message(message)
            self.agent.mark_message_received()
            payload = dict(envelope.payload)
            incident_id = _required_text(payload, "incident_id")
            support_task_id = _required_text(payload, "task_id")
            peer_role = _required_text(payload, "agent_role").lower()
            if peer_role == self.agent.role:
                raise RuntimeError("Peer collaboration result cannot originate from self")

            record = {
                "incident_id": incident_id,
                "support_task_id": support_task_id,
                "peer_role": peer_role,
                "peer_jid": envelope.sender,
                "result": payload,
            }
            self.agent._peer_results_by_incident.setdefault(incident_id, []).append(record)
            self.agent.peer_results_received += 1
            self.agent.last_peer_result = record

            # Keep the peer result in the primary specialist's existing ReAct
            # conversation so later collaboration can reason from both local and
            # peer evidence without exposing hidden chain-of-thought.
            local_result = next(
                (
                    result
                    for result in self.agent._completed_react_results.values()
                    if result.incident_id == incident_id
                ),
                None,
            )
            if local_result is not None:
                self.agent.context.add_message_dict(
                    {
                        "role": "user",
                        "content": (
                            "Direct peer collaboration result received from "
                            f"{peer_role}: "
                            + json.dumps(payload, separators=(",", ":"), sort_keys=True)
                        ),
                    },
                    local_result.conversation_id,
                )

            self.agent.set_activity(
                "WAITING",
                incident_id=incident_id,
                detail="peer_result_received",
            )
            LOGGER.warning(
                "%s received direct peer result from %s incident=%s support_task=%s",
                self.agent.display_name,
                peer_role,
                incident_id,
                support_task_id,
            )
        except Exception as exc:
            LOGGER.exception(
                "%s could not process direct peer result: %s",
                self.agent.display_name,
                exc,
            )


async def share_peer_context(
    agent: Any,
    *,
    receiver: str,
    incident_id: str,
    support_task_id: str,
    target_role: str,
    support_domain: str,
    support_reason: str,
    specialist_result: dict[str, Any],
    timeout: float = 10.0,
) -> AgentMessage:
    if timeout <= 0:
        raise ValueError("Peer collaboration timeout must be greater than zero")

    envelope = AgentMessage.create(
        type=PEER_COLLABORATION_CONTEXT_TYPE,
        sender=str(agent.jid),
        receiver=receiver,
        payload={
            "incident_id": incident_id,
            "support_task_id": support_task_id,
            "peer_role": agent.role,
            "target_role": target_role,
            "authorized_by": "technical_lead",
            "support_domain": support_domain,
            "support_reason": support_reason,
            "specialist_result": dict(specialist_result),
        },
    )
    loop = asyncio.get_running_loop()
    future: asyncio.Future[AgentMessage] = loop.create_future()
    agent._pending_peer_acknowledgements[envelope.correlation_id] = future
    try:
        await agent.send_agent_message(envelope, performative=Performative.REQUEST)
        acknowledgement = await asyncio.wait_for(future, timeout=timeout)
    finally:
        agent._pending_peer_acknowledgements.pop(envelope.correlation_id, None)

    valid = (
        acknowledgement.type == PEER_COLLABORATION_CONTEXT_ACCEPTED_TYPE
        and acknowledgement.sender == receiver
        and acknowledgement.receiver == str(agent.jid)
        and acknowledgement.payload.get("accepted_by") == target_role
        and acknowledgement.payload.get("incident_id") == incident_id
        and acknowledgement.payload.get("support_task_id") == support_task_id
    )
    if not valid:
        raise RuntimeError("Peer specialist returned an invalid collaboration acknowledgement")
    return acknowledgement


async def send_peer_result(
    agent: Any,
    *,
    receiver: str,
    result_payload: dict[str, Any],
    correlation_id: str,
) -> None:
    envelope = AgentMessage.create(
        type=PEER_COLLABORATION_RESULT_TYPE,
        sender=str(agent.jid),
        receiver=receiver,
        correlation_id=correlation_id,
        payload=dict(result_payload),
    )
    await agent.send_agent_message(envelope, performative=Performative.INFORM)
