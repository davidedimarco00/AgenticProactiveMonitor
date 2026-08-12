from __future__ import annotations

import asyncio
import logging
from typing import Any

from spade.behaviour import CyclicBehaviour
from spade.template import Template

from ..communication import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    parse_spade_message,
)
from .base import BaseRoleAgent


LOGGER = logging.getLogger("agentic_system.agents.roles")


class TechnicalLeadAgent(BaseRoleAgent):
    class AcknowledgementBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            try:
                envelope = parse_spade_message(message)
            except (ValueError, TypeError) as exc:
                LOGGER.warning("Technical Lead received invalid AGREE message: %s", exc)
                return

            self.agent.mark_message_received()
            self.agent.last_acknowledgement = envelope

            pending = self.agent._pending_acknowledgements.get(envelope.correlation_id)
            if pending is not None and not pending.done():
                pending.set_result(envelope)

            LOGGER.info(
                "Technical Lead received AGREE/%s from %s correlation_id=%s",
                envelope.type,
                envelope.sender,
                envelope.correlation_id,
            )

    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="technical_lead",
            display_name=display_name,
        )
        self._pending_acknowledgements: dict[
            str, asyncio.Future[AgentMessage]
        ] = {}
        self.last_acknowledgement: AgentMessage | None = None

    async def setup(self) -> None:
        await super().setup()
        template = Template()
        template.set_metadata("protocol", AGENTIC_PROTOCOL)
        template.set_metadata("performative", Performative.AGREE.value)
        self.add_behaviour(self.AcknowledgementBehaviour(), template)

    async def request_specialist(
        self,
        *,
        receiver: str,
        request_type: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> tuple[AgentMessage, AgentMessage]:
        request = AgentMessage.create(
            type=request_type,
            sender=str(self.jid),
            receiver=receiver,
            payload=payload,
        )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMessage] = loop.create_future()
        self._pending_acknowledgements[request.correlation_id] = future

        try:
            await self.send_agent_message(request, performative=Performative.REQUEST)
            acknowledgement = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_acknowledgements.pop(request.correlation_id, None)

        return request, acknowledgement


class SystemEngineerAgent(BaseRoleAgent):
    class RequestBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            try:
                request = parse_spade_message(message)
            except (ValueError, TypeError) as exc:
                LOGGER.warning("System Engineer received invalid REQUEST message: %s", exc)
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
                },
            )
            await self.agent.send_agent_message(
                acknowledgement,
                performative=Performative.AGREE,
            )

            LOGGER.info(
                "System Engineer acknowledged REQUEST/%s from %s correlation_id=%s",
                request.type,
                request.sender,
                request.correlation_id,
            )

    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="system_engineer",
            display_name=display_name,
        )
        self.last_received_request: AgentMessage | None = None

    async def setup(self) -> None:
        await super().setup()
        template = Template()
        template.set_metadata("protocol", AGENTIC_PROTOCOL)
        template.set_metadata("performative", Performative.REQUEST.value)
        self.add_behaviour(self.RequestBehaviour(), template)


class NetworkEngineerAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="network_engineer",
            display_name=display_name,
        )


class ApplicationEngineerAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="application_engineer",
            display_name=display_name,
        )


class SoftwareDeveloperAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="software_developer",
            display_name=display_name,
        )
