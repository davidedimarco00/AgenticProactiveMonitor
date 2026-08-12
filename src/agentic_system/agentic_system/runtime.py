from __future__ import annotations

import asyncio
import logging
from typing import Any

from .agents.base import BaseRoleAgent
from .agents.factory import build_agents
from .agents.roles import SystemEngineerAgent, TechnicalLeadAgent
from .communication import Performative
from .config import RuntimeConfig


LOGGER = logging.getLogger("agentic_system.runtime")


class AgentRuntime:
    """Owns the five SPADE agents hosted by the single backend container."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.agents = build_agents(config.agents)
        self.started = False
        self.communication_probe: dict[str, Any] | None = None

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

        LOGGER.info("All %d SPADE agents are connected", len(self.agents))

        try:
            self.communication_probe = await self._run_communication_probe()
        except BaseException:
            await self.stop()
            raise

        self.started = True
        LOGGER.info("Inter-agent XMPP communication probe passed")

    async def _run_communication_probe(self) -> dict[str, Any]:
        """Verify the real Technical Lead -> System Engineer -> Technical Lead path.

        This is a transport/readiness probe only. It does not perform BDI reasoning,
        tool selection or diagnosis.
        """

        technical_lead = next(
            (agent for agent in self.agents if agent.role == "technical_lead"), None
        )
        system_engineer = next(
            (agent for agent in self.agents if agent.role == "system_engineer"), None
        )

        if not isinstance(technical_lead, TechnicalLeadAgent):
            raise RuntimeError("Technical Lead SPADE agent is not available")
        if not isinstance(system_engineer, SystemEngineerAgent):
            raise RuntimeError("System Engineer SPADE agent is not available")

        request, acknowledgement = await technical_lead.request_specialist(
            receiver=str(system_engineer.jid),
            request_type="runtime_connectivity_probe",
            payload={
                "purpose": "verify_inter_agent_xmpp",
                "requested_role": system_engineer.role,
            },
            timeout=5.0,
        )

        if acknowledgement.correlation_id != request.correlation_id:
            raise RuntimeError("XMPP acknowledgement correlation_id does not match request")
        if acknowledgement.sender != str(system_engineer.jid):
            raise RuntimeError("XMPP acknowledgement came from an unexpected agent")
        if acknowledgement.receiver != str(technical_lead.jid):
            raise RuntimeError("XMPP acknowledgement targets an unexpected agent")
        if acknowledgement.payload.get("accepted_by") != system_engineer.role:
            raise RuntimeError("System Engineer acknowledgement payload is invalid")

        return {
            "status": "passed",
            "protocol": "agentic-proactive-monitor/v1",
            "request_performative": Performative.REQUEST.value,
            "response_performative": Performative.AGREE.value,
            "request_type": request.type,
            "response_type": acknowledgement.type,
            "sender": request.sender,
            "receiver": request.receiver,
            "response_sender": acknowledgement.sender,
            "response_receiver": acknowledgement.receiver,
            "request_correlation_id": request.correlation_id,
            "response_correlation_id": acknowledgement.correlation_id,
            "acknowledged_by": acknowledgement.payload.get("accepted_by"),
        }

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

    def snapshot(self) -> list[dict[str, Any]]:
        return [agent.snapshot() for agent in self.agents]
