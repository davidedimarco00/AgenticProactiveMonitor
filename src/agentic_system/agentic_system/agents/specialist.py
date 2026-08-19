from __future__ import annotations

import logging

from spade.behaviour import CyclicBehaviour
from spade.template import Template
from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from .base import BaseAgent
from .messages import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    parse_spade_message,
)


LOGGER = logging.getLogger("agentic_system.agents.specialist")
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"


class SpecialistAgent(BaseAgent):
    """Shared XMPP health-probe behaviour for the four specialist LLM agents."""

    class HealthProbeRequestBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            try:
                request = parse_spade_message(message)
            except (ValueError, TypeError) as exc:
                LOGGER.warning(
                    "%s received invalid health REQUEST: %s",
                    self.agent.display_name,
                    exc,
                )
                return

            self.agent.mark_message_received()
            self.agent.last_received_request = request

            acknowledgement = AgentMessage.create(
                type="request_acknowledged",
                sender=str(self.agent.jid),
                receiver=request.sender,
                correlation_id=request.correlation_id,
                payload={
                    "accepted_by": self.agent.role,
                    "request_type": request.type,
                    "health": True,
                },
            )
            await self.agent.send_agent_message(
                acknowledgement,
                performative=Performative.AGREE,
            )

            LOGGER.info(
                "%s acknowledged health REQUEST from %s correlation_id=%s",
                self.agent.display_name,
                request.sender,
                request.correlation_id,
            )

    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
        *,
        role: str,
        system_prompt: str,
        provider: LLMProvider,
        mcp_servers: list[MCPServerConfig],
        interaction_memory_path: str,
    ) -> None:
        super().__init__(
            jid,
            password,
            role=role,
            display_name=display_name,
            health_port=health_port,
            provider=provider,
            mcp_servers=mcp_servers,
            system_prompt=system_prompt,
            interaction_memory_path=interaction_memory_path,
        )
        self.last_received_request: AgentMessage | None = None

    async def setup(self) -> None:
        await super().setup()
        template = Template()
        template.set_metadata("protocol", AGENTIC_PROTOCOL)
        template.set_metadata("performative", Performative.REQUEST.value)
        template.set_metadata("message_type", HEALTH_PROBE_MESSAGE_TYPE)
        self.add_behaviour(self.HealthProbeRequestBehaviour(), template)
