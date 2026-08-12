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
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"
HEALTH_PROBE_INTERVAL_SECONDS = 5.0
HEALTH_PROBE_TIMEOUT_SECONDS = 3.0


class AgentRuntime:
    """Owns the five SPADE agents hosted by the single backend container."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.agents = build_agents(config.agents)
        self.started = False
        self.communication_probe: dict[str, Any] | None = None
        self._health_probe_task: asyncio.Task[None] | None = None

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
            # Preserve TEST E's explicit Technical Lead <-> System Engineer round-trip.
            self.communication_probe = await self._run_communication_probe()
            # Then prove that every specialist can exchange a real XMPP message with
            # the Technical Lead before marking the backend ready.
            await self._run_health_probe_cycle(strict=True)
        except BaseException:
            await self.stop()
            raise

        self.started = True
        self._health_probe_task = asyncio.create_task(
            self._health_probe_loop(),
            name="agent-xmpp-health-probe",
        )
        LOGGER.info("Inter-agent XMPP communication probe passed for all five agents")

    def _technical_lead(self) -> TechnicalLeadAgent:
        agent = next(
            (agent for agent in self.agents if agent.role == "technical_lead"), None
        )
        if not isinstance(agent, TechnicalLeadAgent):
            raise RuntimeError("Technical Lead SPADE agent is not available")
        return agent

    def _specialists(self) -> list[BaseRoleAgent]:
        return [agent for agent in self.agents if agent.role != "technical_lead"]

    async def _run_communication_probe(self) -> dict[str, Any]:
        """Verify the original TEST E Technical Lead/System Engineer path."""

        technical_lead = self._technical_lead()
        system_engineer = next(
            (agent for agent in self.agents if agent.role == "system_engineer"), None
        )

        if not isinstance(system_engineer, SystemEngineerAgent):
            raise RuntimeError("System Engineer SPADE agent is not available")

        request, acknowledgement = await technical_lead.request_specialist(
            receiver=str(system_engineer.jid),
            request_type=HEALTH_PROBE_MESSAGE_TYPE,
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

    async def _probe_specialist(
        self,
        technical_lead: TechnicalLeadAgent,
        specialist: BaseRoleAgent,
    ) -> bool:
        try:
            request, acknowledgement = await technical_lead.request_specialist(
                receiver=str(specialist.jid),
                request_type=HEALTH_PROBE_MESSAGE_TYPE,
                payload={
                    "purpose": "agent_health",
                    "requested_role": specialist.role,
                },
                timeout=HEALTH_PROBE_TIMEOUT_SECONDS,
            )

            valid = (
                acknowledgement.correlation_id == request.correlation_id
                and acknowledgement.sender == str(specialist.jid)
                and acknowledgement.receiver == str(technical_lead.jid)
                and acknowledgement.payload.get("accepted_by") == specialist.role
            )
            if not valid:
                specialist.mark_communication_failed()
                LOGGER.warning(
                    "Health probe returned invalid acknowledgement for %s",
                    specialist.role,
                )
                return False

            specialist.mark_communication_ok()
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            specialist.mark_communication_failed()
            LOGGER.warning("Health probe failed for %s: %s", specialist.role, exc)
            return False

    async def _run_health_probe_cycle(self, *, strict: bool) -> None:
        technical_lead = self._technical_lead()
        failures: list[str] = []

        for specialist in self._specialists():
            if not await self._probe_specialist(technical_lead, specialist):
                failures.append(specialist.role)

        if failures:
            technical_lead.mark_communication_failed()
            if strict:
                raise RuntimeError(
                    "XMPP health probe failed for specialist agents: "
                    + ", ".join(failures)
                )
        else:
            technical_lead.mark_communication_ok()

    async def _health_probe_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEALTH_PROBE_INTERVAL_SECONDS)
                await self._run_health_probe_cycle(strict=False)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        if self._health_probe_task is not None:
            self._health_probe_task.cancel()
            try:
                await self._health_probe_task
            except asyncio.CancelledError:
                pass
            self._health_probe_task = None

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
