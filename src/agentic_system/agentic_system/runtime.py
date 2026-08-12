from __future__ import annotations

import asyncio
import logging

from .agents.base import BaseRoleAgent
from .agents.factory import build_agents
from .config import RuntimeConfig


LOGGER = logging.getLogger("agentic_system.runtime")


class AgentRuntime:
    """Owns the five SPADE agents hosted by the single backend container."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.agents = build_agents(config.agents)
        self.started = False

    async def start(self) -> None:
        LOGGER.info("Starting %d SPADE agents", len(self.agents))

        results = await asyncio.gather(
            *(
                agent.start(auto_register=self.config.xmpp_auto_register)
                for agent in self.agents
            ),
            return_exceptions=True,
        )

        failures: list[str] = []
        for agent, result in zip(self.agents, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(f"{agent.role}: {result}")

        if failures:
            await self.stop()
            raise RuntimeError(
                "One or more SPADE agents failed to start: " + "; ".join(failures)
            )

        self.started = True
        LOGGER.info("All %d SPADE agents are connected", len(self.agents))

    async def stop(self) -> None:
        if not self.agents:
            return

        LOGGER.info("Stopping SPADE agents")
        for agent in self.agents:
            agent.mark_stopping()

        results = await asyncio.gather(
            *(agent.stop() for agent in self.agents),
            return_exceptions=True,
        )

        for agent, result in zip(self.agents, results, strict=True):
            if isinstance(result, BaseException):
                LOGGER.warning("Failed to stop %s cleanly: %s", agent.role, result)
            agent.mark_stopped()

        self.started = False

    @property
    def running_count(self) -> int:
        return sum(agent.lifecycle_state == "running" for agent in self.agents)

    def snapshot(self) -> list[dict[str, str | None]]:
        return [agent.snapshot() for agent in self.agents]
