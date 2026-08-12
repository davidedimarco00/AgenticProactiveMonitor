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
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"


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

    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            role="technical_lead",
            display_name=display_name,
            health_port=health_port,
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


class _AcknowledgingSpecialistAgent(BaseRoleAgent):
    """Common health-probe transport behaviour for specialist SPADE agents.

    This is deliberately limited to the runtime connectivity probe. Future BDI or
    diagnostic REQUEST messages will use their own message types and behaviours.
    """

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
    ) -> None:
        super().__init__(
            jid,
            password,
            role=role,
            display_name=display_name,
            health_port=health_port,
        )
        self.last_received_request: AgentMessage | None = None

    async def setup(self) -> None:
        await super().setup()
        template = Template()
        template.set_metadata("protocol", AGENTIC_PROTOCOL)
        template.set_metadata("performative", Performative.REQUEST.value)
        template.set_metadata("message_type", HEALTH_PROBE_MESSAGE_TYPE)
        self.add_behaviour(self.HealthProbeRequestBehaviour(), template)


class SystemEngineerAgent(_AcknowledgingSpecialistAgent):
    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            display_name,
            health_port,
            role="system_engineer",
        )


class NetworkEngineerAgent(_AcknowledgingSpecialistAgent):
    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            display_name,
            health_port,
            role="network_engineer",
        )


class ApplicationEngineerAgent(_AcknowledgingSpecialistAgent):
    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            display_name,
            health_port,
            role="application_engineer",
        )


class SoftwareDeveloperAgent(_AcknowledgingSpecialistAgent):
    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            display_name,
            health_port,
            role="software_developer",
        )
