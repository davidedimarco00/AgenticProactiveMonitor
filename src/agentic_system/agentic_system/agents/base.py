from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour

from ..communication import AgentMessage, Performative, build_spade_message


LOGGER = logging.getLogger("agentic_system.agents")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseRoleAgent(Agent):
    """Common SPADE runtime for every logical role in the MAS.

    This layer provides lifecycle and transport concerns only. BDI deliberation and
    ReAct execution are added later instead of being simulated here.
    """

    class LifecycleBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            self.agent.last_heartbeat_at = _utc_now()
            await asyncio.sleep(5)

    class SendMessageBehaviour(OneShotBehaviour):
        """Send one semantic AgentMessage through SPADE's Behaviour API.

        SPADE exposes message sending on Behaviour.send(), not directly on Agent.
        Keeping transport inside a real behaviour preserves the agent/behaviour
        execution model while still exposing a convenient agent-level helper to
        higher architectural layers.
        """

        def __init__(
            self,
            envelope: AgentMessage,
            performative: Performative,
        ) -> None:
            super().__init__()
            self.envelope = envelope
            self.performative = performative

        async def run(self) -> None:
            message = build_spade_message(
                self.envelope,
                performative=self.performative,
            )
            await self.send(message)
            self.agent.mark_message_sent()
            LOGGER.info(
                "%s sent %s/%s to %s correlation_id=%s",
                self.agent.display_name,
                self.performative.value,
                self.envelope.type,
                self.envelope.receiver,
                self.envelope.correlation_id,
            )

    def __init__(
        self,
        jid: str,
        password: str,
        *,
        role: str,
        display_name: str,
    ) -> None:
        super().__init__(jid, password, verify_security=False)
        self.role = role
        self.display_name = display_name
        self.lifecycle_state = "created"
        self.started_at: str | None = None
        self.last_heartbeat_at: str | None = None
        self.messages_sent = 0
        self.messages_received = 0
        self.last_message_at: str | None = None

    async def setup(self) -> None:
        self.lifecycle_state = "running"
        self.started_at = _utc_now()
        self.last_heartbeat_at = self.started_at
        self.add_behaviour(self.LifecycleBehaviour())
        LOGGER.info(
            "%s connected to XMPP as %s",
            self.display_name,
            self.jid,
        )

    async def send_agent_message(
        self,
        envelope: AgentMessage,
        *,
        performative: Performative,
        timeout: float = 5.0,
    ) -> None:
        """Send a semantic message using a one-shot SPADE behaviour.

        Higher layers can request a send from the agent object, but the actual XMPP
        send is always executed by a SPADE Behaviour, as required by SPADE's API.
        """

        behaviour = self.SendMessageBehaviour(envelope, performative)
        self.add_behaviour(behaviour)
        await behaviour.join(timeout=timeout)

        exit_code = behaviour.exit_code
        if isinstance(exit_code, BaseException):
            raise exit_code

    def mark_message_sent(self) -> None:
        self.messages_sent += 1
        self.last_message_at = _utc_now()

    def mark_message_received(self) -> None:
        self.messages_received += 1
        self.last_message_at = _utc_now()

    def mark_stopping(self) -> None:
        self.lifecycle_state = "stopping"

    def mark_stopped(self) -> None:
        self.lifecycle_state = "stopped"

    def snapshot(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "display_name": self.display_name,
            "jid": str(self.jid),
            "state": self.lifecycle_state,
            "started_at": self.started_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "last_message_at": self.last_message_at,
        }
