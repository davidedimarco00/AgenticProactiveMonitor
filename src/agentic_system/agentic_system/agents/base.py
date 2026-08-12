from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour

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
    ) -> None:
        message = build_spade_message(envelope, performative=performative)
        await self.send(message)
        self.messages_sent += 1
        self.last_message_at = _utc_now()
        LOGGER.info(
            "%s sent %s/%s to %s correlation_id=%s",
            self.display_name,
            performative.value,
            envelope.type,
            envelope.receiver,
            envelope.correlation_id,
        )

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
