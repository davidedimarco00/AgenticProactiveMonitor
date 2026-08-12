from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour


LOGGER = logging.getLogger("agentic_system.agents")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseRoleAgent(Agent):
    """Common SPADE runtime for every logical role in the MAS.

    This class intentionally contains lifecycle concerns only. BDI state and ReAct
    reasoning are added in later layers instead of being simulated here.
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

    def mark_stopping(self) -> None:
        self.lifecycle_state = "stopping"

    def mark_stopped(self) -> None:
        self.lifecycle_state = "stopped"

    def snapshot(self) -> dict[str, str | None]:
        return {
            "role": self.role,
            "display_name": self.display_name,
            "jid": str(self.jid),
            "state": self.lifecycle_state,
            "started_at": self.started_at,
            "last_heartbeat_at": self.last_heartbeat_at,
        }
