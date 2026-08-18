from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ..agents.base import BaseAgent
from ..agents.factory import build_agents
from ..agents.roles import SystemEngineerAgent, TechnicalLeadAgent
from ..application.anomaly_ingestion import AnomalyIntake
from ..application.ports.incident_assignee import (
    IncidentAssigneeReceipt,
    IncidentTriageReceipt,
)
from ..communication import Performative
from ..config import RuntimeConfig
from ..infrastructure.opensearch import AnomalyObservation, OpenSearchAnomalyWatcher


LOGGER = logging.getLogger("agentic_system.runtime")
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"
HEALTH_PROBE_INTERVAL_SECONDS = 5.0
HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
ANOMALY_QUEUE_MAXSIZE = 256
AnomalyHandler = Callable[[AnomalyObservation], Awaitable[object]]


class AgentRuntime:
    """Owns the five SPADE-LLM agents hosted by the backend container."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        anomaly_handler: AnomalyHandler | None = None,
    ) -> None:
        self.config = config
        self.agents = build_agents(config)
        self.started = False
        self.communication_probe: dict[str, Any] | None = None
        self.team_communication_ok = False
        self.unreachable_specialists: list[str] = []
        self._health_probe_task: asyncio.Task[None] | None = None
        self._anomaly_watcher_task: asyncio.Task[None] | None = None
        self._anomaly_intake_task: asyncio.Task[None] | None = None

        self.anomaly_queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(
            maxsize=ANOMALY_QUEUE_MAXSIZE
        )
        self.anomaly_intake = AnomalyIntake(
            self.anomaly_queue,
            on_anomaly=anomaly_handler,
        )
        self.anomaly_watcher = OpenSearchAnomalyWatcher(
            opensearch_url=config.opensearch_url,
            on_anomaly=self.anomaly_queue.put,
            poll_interval_seconds=config.anomaly_watch_poll_seconds,
            lookback_seconds=config.anomaly_watch_lookback_seconds,
        )

    def configure_anomaly_handler(self, handler: AnomalyHandler) -> None:
        """Attach the application workflow before the runtime is started."""

        if self.started:
            raise RuntimeError("Anomaly handler must be configured before runtime start")
        self.anomaly_intake.on_anomaly = handler

    async def assign_incident(
        self,
        incident: dict[str, Any],
    ) -> IncidentAssigneeReceipt:
        """Assign one persisted incident to the Technical Lead SPADE behaviour."""

        technical_lead = self._technical_lead()
        assignment = await technical_lead.submit_incident(incident)
        return IncidentAssigneeReceipt(
            incident_id=assignment.incident_id,
            agent_role=technical_lead.role,
            agent_jid=str(technical_lead.jid),
        )

    async def triage_incident(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
    ) -> IncidentTriageReceipt:
        """Run TL first analysis and Jason BDI commitment without delegating yet."""

        technical_lead = self._technical_lead()
        available_agents = [
            specialist.role
            for specialist in self._specialists()
            if specialist.xmpp_connected and specialist.communication_ok
        ]
        decision = await technical_lead.triage_incident(
            incident,
            detector_context=detector_context,
            available_agents=available_agents,
        )
        return IncidentTriageReceipt(
            incident_id=decision.incident_id,
            probable_domain=decision.probable_domain,
            primary_investigator=decision.primary_investigator,
            confidence=decision.confidence,
            rationale=decision.rationale,
            bdi_goal=decision.bdi_goal,
            bdi_intention=decision.bdi_intention,
        )

    async def start(self) -> None:
        LOGGER.info("Starting %d SPADE-LLM agents", len(self.agents))

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
                "One or more SPADE-LLM agents failed to start: " + "; ".join(failures)
            )

        LOGGER.info("All %d SPADE-LLM agents are connected", len(self.agents))

        try:
            self.communication_probe = await self._run_communication_probe()
            await self._run_health_probe_cycle(strict=True)
        except BaseException:
            await self.stop()
            raise

        self.started = True
        self._health_probe_task = asyncio.create_task(
            self._health_probe_loop(),
            name="agent-xmpp-health-probe",
        )
        self._anomaly_intake_task = asyncio.create_task(
            self.anomaly_intake.run(),
            name="anomaly-intake-worker",
        )
        self._anomaly_watcher_task = asyncio.create_task(
            self.anomaly_watcher.run(),
            name="opensearch-anomaly-watcher",
        )
        LOGGER.info("Inter-agent XMPP communication probe passed for all five agents")
        LOGGER.info("Queued OpenSearch anomaly intake attached to the agentic runtime")

    @property
    def anomalies_received(self) -> int:
        return self.anomaly_intake.processed_count

    @property
    def last_anomaly(self) -> AnomalyObservation | None:
        return self.anomaly_intake.last_anomaly

    def anomaly_watch_snapshot(self) -> dict[str, Any]:
        return {
            "running": self.anomaly_watcher.running,
            "opensearch_url": self.config.opensearch_url,
            "poll_interval_seconds": self.config.anomaly_watch_poll_seconds,
            "lookback_seconds": self.config.anomaly_watch_lookback_seconds,
            "poll_count": self.anomaly_watcher.poll_count,
            "intake_running": self.anomaly_intake.running,
            "queue_depth": self.anomaly_queue.qsize(),
            "queue_maxsize": self.anomaly_queue.maxsize,
            "anomalies_received": self.anomalies_received,
            "last_error": self.anomaly_watcher.last_error,
            "intake_last_error": self.anomaly_intake.last_error,
            "last_anomaly": (
                self.last_anomaly.to_dict() if self.last_anomaly is not None else None
            ),
        }

    def _technical_lead(self) -> TechnicalLeadAgent:
        agent = next(
            (agent for agent in self.agents if agent.role == "technical_lead"), None
        )
        if not isinstance(agent, TechnicalLeadAgent):
            raise RuntimeError("Technical Lead SPADE-LLM agent is not available")
        return agent

    def _specialists(self) -> list[BaseAgent]:
        return [agent for agent in self.agents if agent.role != "technical_lead"]

    async def _run_communication_probe(self) -> dict[str, Any]:
        technical_lead = self._technical_lead()
        system_engineer = next(
            (agent for agent in self.agents if agent.role == "system_engineer"), None
        )

        if not isinstance(system_engineer, SystemEngineerAgent):
            raise RuntimeError("System Engineer SPADE-LLM agent is not available")

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
        specialist: BaseAgent,
    ) -> bool:
        if not specialist.xmpp_connected:
            specialist.mark_communication_failed()
            LOGGER.warning(
                "Health probe skipped for %s because its XMPP session is disconnected",
                specialist.role,
            )
            return False

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

            if not specialist.xmpp_connected:
                specialist.mark_communication_failed()
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

        self.unreachable_specialists = failures
        self.team_communication_ok = not failures

        if technical_lead.xmpp_connected:
            technical_lead.mark_communication_ok()
        else:
            technical_lead.mark_communication_failed()

        if failures and strict:
            raise RuntimeError(
                "XMPP health probe failed for specialist agents: "
                + ", ".join(failures)
            )

    async def _health_probe_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEALTH_PROBE_INTERVAL_SECONDS)
                await self._run_health_probe_cycle(strict=False)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        if self._anomaly_watcher_task is not None:
            self.anomaly_watcher.stop()
            try:
                await self._anomaly_watcher_task
            except asyncio.CancelledError:
                pass
            self._anomaly_watcher_task = None

        if self._anomaly_intake_task is not None:
            await self.anomaly_queue.join()
            self._anomaly_intake_task.cancel()
            try:
                await self._anomaly_intake_task
            except asyncio.CancelledError:
                pass
            self._anomaly_intake_task = None

        if self._health_probe_task is not None:
            self._health_probe_task.cancel()
            try:
                await self._health_probe_task
            except asyncio.CancelledError:
                pass
            self._health_probe_task = None

        if not self.agents:
            return

        LOGGER.info("Stopping SPADE-LLM agents")
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
        self.team_communication_ok = False
        self.unreachable_specialists = [
            agent.role for agent in self.agents if agent.role != "technical_lead"
        ]

    @property
    def running_count(self) -> int:
        return sum(agent.lifecycle_state == "running" for agent in self.agents)

    def snapshot(self) -> list[dict[str, Any]]:
        return [agent.snapshot() for agent in self.agents]
