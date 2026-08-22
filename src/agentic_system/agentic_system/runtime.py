from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .agents.base import BaseAgent
from .agents.factory import build_agents
from .agents.investigation_results import (
    install_investigation_result_inbox,
    pop_investigation_result,
)
from .agents.messages import Performative
from .agents.specialist import (
    INVESTIGATION_TASK_ACCEPTED_TYPE,
    INVESTIGATION_TASK_EXECUTION_FAILED_TYPE,
    INVESTIGATION_TASK_MESSAGE_TYPE,
    INVESTIGATION_TASK_RESULT_TYPE,
    SpecialistAgent,
)
from .agents.technical_lead import TechnicalLeadAgent
from .incidents import (
    AnomalyIntake,
    AnomalyObservation,
    IncidentAssigneeReceipt,
    IncidentTriageReceipt,
    InvestigationTaskDispatchReceipt,
    InvestigationTaskResultReceipt,
)
from .integrations import OpenSearchAnomalyWatcher
from .settings import RuntimeConfig


LOGGER = logging.getLogger("agentic_system.runtime")
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"
HEALTH_PROBE_INTERVAL_SECONDS = 30.0
HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
ANOMALY_QUEUE_MAXSIZE = 4096
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

        # This is the global agentic-system work queue. One consumer owns one
        # anomaly until the complete collaborative handler returns.
        self.anomaly_queue: asyncio.Queue[AnomalyObservation] = asyncio.Queue(
            maxsize=ANOMALY_QUEUE_MAXSIZE
        )
        self.anomaly_intake = AnomalyIntake(
            self.anomaly_queue,
            on_anomaly=anomaly_handler,
        )
        self.anomaly_watcher = OpenSearchAnomalyWatcher(
            opensearch_url=config.opensearch_url,
            on_anomaly=self.anomaly_intake.enqueue,
            poll_interval_seconds=config.anomaly_watch_poll_seconds,
            lookback_seconds=config.anomaly_watch_lookback_seconds,
        )

    def configure_anomaly_handler(self, handler: AnomalyHandler) -> None:
        """Attach the application workflow before runtime start."""

        if self.started:
            raise RuntimeError("Anomaly handler must be configured before runtime start")
        self.anomaly_intake.on_anomaly = handler

    async def enqueue_recovery_observations(
        self,
        observations: list[AnomalyObservation],
    ) -> int:
        """Seed durable recovery work before fresh OpenSearch polling begins."""

        accepted = 0
        for observation in observations:
            if await self.anomaly_intake.enqueue(observation):
                accepted += 1
        if accepted:
            LOGGER.warning(
                "Seeded %d durable incident recovery item(s) into the exclusive FIFO",
                accepted,
            )
        return accepted

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
        """Run BDI-led TL triage and select the primary specialist."""

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
            bdi_triage_intention=decision.bdi_triage_intention,
            bdi_intention=decision.bdi_intention,
        )

    async def dispatch_investigation_task(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
    ) -> InvestigationTaskDispatchReceipt:
        """Delegate one durable DISPATCHED task through TL -> specialist XMPP."""

        if not self.started:
            raise RuntimeError("Agent runtime is not running")

        task_id = str(task.get("task_id") or "").strip()
        incident_id = str(task.get("incident_id") or "").strip()
        assigned_to = str(task.get("assigned_to") or "").strip().lower()
        task_type = str(task.get("task_type") or "").strip().upper()
        state = str(task.get("state") or "").strip().upper()
        if not task_id or not incident_id or not assigned_to:
            raise ValueError("Durable task is missing dispatch identity fields")
        if incident_id != str(incident.get("incident_id") or "").strip():
            raise RuntimeError("Task incident_id does not match the incident being dispatched")
        if state != "DISPATCHED":
            raise RuntimeError(
                f"Task {task_id} must be DISPATCHED before XMPP delivery, got {state}"
            )

        technical_lead = self._technical_lead()
        specialist = self._specialist_by_role(assigned_to)
        if not specialist.xmpp_connected or not specialist.communication_ok:
            raise RuntimeError(f"Selected specialist {assigned_to} is not reachable")

        request, acknowledgement = await technical_lead.request_specialist(
            receiver=str(specialist.jid),
            request_type=INVESTIGATION_TASK_MESSAGE_TYPE,
            payload={
                "task_id": task_id,
                "incident_id": incident_id,
                "task_type": task_type,
                "assigned_to": assigned_to,
                "attempt": int(task.get("attempt") or 0),
                "max_attempts": int(task.get("max_attempts") or 0),
                "severity": str(incident.get("severity") or "MEDIUM").upper(),
                "entity": str(incident.get("entity") or "unknown"),
                "anomaly": dict(incident.get("anomaly") or {}),
            },
            timeout=self.config.task_dispatch_timeout_seconds,
        )

        valid = (
            acknowledgement.type == INVESTIGATION_TASK_ACCEPTED_TYPE
            and acknowledgement.correlation_id == request.correlation_id
            and acknowledgement.sender == str(specialist.jid)
            and acknowledgement.receiver == str(technical_lead.jid)
            and acknowledgement.payload.get("accepted_by") == specialist.role
            and acknowledgement.payload.get("task_id") == task_id
            and acknowledgement.payload.get("incident_id") == incident_id
        )
        if not valid:
            specialist.mark_communication_failed()
            raise RuntimeError(
                f"Specialist {assigned_to} returned an invalid task acknowledgement"
            )

        specialist.mark_communication_ok()
        return InvestigationTaskDispatchReceipt(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=specialist.role,
            agent_jid=str(specialist.jid),
            correlation_id=request.correlation_id,
            bdi_goal=str(acknowledgement.payload.get("bdi_goal") or ""),
            bdi_acceptance_intention=str(
                acknowledgement.payload.get("bdi_acceptance_intention") or ""
            ),
            bdi_investigation_intention=str(
                acknowledgement.payload.get("bdi_investigation_intention") or ""
            ),
        )

    async def collect_investigation_result(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
    ) -> InvestigationTaskResultReceipt | None:
        """Collect one asynchronous specialist ReAct outcome from the TL inbox."""

        task_id = str(task.get("task_id") or "").strip()
        incident_id = str(task.get("incident_id") or "").strip()
        assigned_to = str(task.get("assigned_to") or "").strip().lower()
        if not task_id or not incident_id or not assigned_to:
            raise ValueError("Task result collection requires durable task identity")
        if incident_id != str(incident.get("incident_id") or "").strip():
            raise RuntimeError("Task result incident_id does not match current incident")

        technical_lead = self._technical_lead()
        message = pop_investigation_result(technical_lead, task_id)
        if message is None:
            return None

        specialist = self._specialist_by_role(assigned_to)
        payload = dict(message.payload)
        valid_identity = (
            message.sender == str(specialist.jid)
            and message.receiver == str(technical_lead.jid)
            and str(payload.get("task_id") or "") == task_id
            and str(payload.get("incident_id") or "") == incident_id
        )
        if not valid_identity:
            raise RuntimeError(
                f"Specialist {assigned_to} returned an invalid investigation result identity"
            )

        if message.type == INVESTIGATION_TASK_EXECUTION_FAILED_TYPE:
            return InvestigationTaskResultReceipt(
                task_id=task_id,
                incident_id=incident_id,
                agent_role=assigned_to,
                agent_jid=str(specialist.jid),
                correlation_id=message.correlation_id,
                succeeded=False,
                error=str(payload.get("error") or "Specialist ReAct execution failed."),
                retryable=bool(payload.get("retryable", True)),
            )

        if message.type != INVESTIGATION_TASK_RESULT_TYPE:
            raise RuntimeError(f"Unsupported specialist result type: {message.type}")

        result_role = str(payload.get("agent_role") or "").strip().lower()
        if result_role != assigned_to:
            raise RuntimeError(
                f"Specialist result role mismatch: expected {assigned_to}, got {result_role}"
            )

        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Specialist result confidence is invalid") from exc
        if not 0.0 <= confidence <= 1.0:
            raise RuntimeError("Specialist result confidence must be between 0 and 1")

        def strings(name: str) -> tuple[str, ...]:
            value = payload.get(name) or []
            if not isinstance(value, list):
                raise RuntimeError(f"Specialist result {name} must be an array")
            return tuple(str(item).strip() for item in value if str(item).strip())

        diagnosis_status = str(payload.get("diagnosis_status") or "").strip().lower()
        if diagnosis_status not in {"confirmed", "probable", "inconclusive"}:
            raise RuntimeError(
                f"Specialist result diagnosis_status is invalid: {diagnosis_status!r}"
            )
        root_cause_raw = payload.get("root_cause")
        root_cause = (
            str(root_cause_raw).strip() if root_cause_raw is not None else None
        )
        if root_cause in {"", "none", "null", "unknown", "unconfirmed"}:
            root_cause = None
        causal_chain = strings("causal_chain")

        evidence_raw = payload.get("evidence") or []
        if not isinstance(evidence_raw, list):
            raise RuntimeError("Specialist result evidence must be an array")
        evidence = tuple(dict(item) for item in evidence_raw if isinstance(item, dict))

        react_steps = int(payload.get("react_steps") or 0)
        if react_steps <= 0:
            raise RuntimeError("Specialist result react_steps must be greater than zero")

        assistance_required = payload.get("assistance_required", False)
        if not isinstance(assistance_required, bool):
            raise RuntimeError("Specialist result assistance_required must be boolean")
        assistance_domain = (
            str(payload.get("assistance_domain")).strip().lower()
            if payload.get("assistance_domain") is not None
            else None
        )
        if assistance_domain in {"", "none", "null"}:
            assistance_domain = None

        if diagnosis_status in {"confirmed", "probable"}:
            if not root_cause:
                raise RuntimeError(
                    f"Specialist {diagnosis_status} result requires root_cause"
                )
            if not causal_chain:
                raise RuntimeError(
                    f"Specialist {diagnosis_status} result requires causal_chain"
                )
        if diagnosis_status == "confirmed":
            if assistance_required or assistance_domain is not None:
                raise RuntimeError(
                    "Confirmed specialist diagnosis cannot request diagnostic assistance"
                )
        elif not assistance_required or assistance_domain is None:
            raise RuntimeError(
                "Probable or inconclusive specialist diagnosis must request assistance"
            )

        return InvestigationTaskResultReceipt(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=assigned_to,
            agent_jid=str(specialist.jid),
            correlation_id=message.correlation_id,
            succeeded=True,
            summary=str(payload.get("summary") or "").strip(),
            diagnosis_status=diagnosis_status,
            root_cause=root_cause,
            causal_chain=causal_chain,
            confidence=confidence,
            findings=strings("findings"),
            evidence=evidence,
            hypotheses=strings("hypotheses"),
            recommended_next_steps=strings("recommended_next_steps"),
            assistance_required=assistance_required,
            assistance_domain=assistance_domain,
            react_steps=react_steps,
            tools_used=strings("tools_used"),
            conversation_id=str(payload.get("conversation_id") or "").strip() or None,
            retryable=False,
        )

    async def start(self, *, start_observation_pipeline: bool = True) -> None:
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

        # Specialist results are asynchronous INFORM/FAILURE messages that arrive
        # after the initial task AGREE. Install the TL result inbox before any
        # incident can be dispatched so no outcome can race ahead of collection.
        install_investigation_result_inbox(self._technical_lead())

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
        if start_observation_pipeline:
            self.start_observation_pipeline()
        LOGGER.info("Inter-agent XMPP communication probe passed for all five agents")

    def start_observation_pipeline(self) -> None:
        """Start exclusive anomaly intake and fresh OpenSearch polling."""

        if not self.started:
            raise RuntimeError("Agent runtime must be started before anomaly intake")
        if self._anomaly_intake_task is None:
            self._anomaly_intake_task = asyncio.create_task(
                self.anomaly_intake.run(),
                name="anomaly-intake-worker",
            )
        if self.config.enable_opensearch_anomaly_watcher and self._anomaly_watcher_task is None:
            self._anomaly_watcher_task = asyncio.create_task(
                self.anomaly_watcher.run(),
                name="opensearch-anomaly-watcher",
            )
        LOGGER.info(
            "Global FIFO anomaly pipeline started with concurrency=1; OpenSearch watcher=%s",
            "enabled" if self.config.enable_opensearch_anomaly_watcher else "disabled",
        )

    @property
    def anomalies_received(self) -> int:
        return self.anomaly_intake.processed_count

    @property
    def last_anomaly(self) -> AnomalyObservation | None:
        return self.anomaly_intake.last_anomaly

    def anomaly_watch_snapshot(self) -> dict[str, Any]:
        return {
            "running": self.anomaly_watcher.running,
            "processing_mode": "FIFO_SINGLE_ACTIVE",
            "max_concurrent_anomalies": 1,
            "opensearch_url": self.config.opensearch_url,
            "poll_interval_seconds": self.config.anomaly_watch_poll_seconds,
            "lookback_seconds": self.config.anomaly_watch_lookback_seconds,
            "poll_count": self.anomaly_watcher.poll_count,
            "intake_running": self.anomaly_intake.running,
            "queue_depth": self.anomaly_queue.qsize(),
            "queue_maxsize": self.anomaly_queue.maxsize,
            "enqueued_count": self.anomaly_intake.enqueued_count,
            "duplicate_count": self.anomaly_intake.duplicate_count,
            "anomalies_received": self.anomalies_received,
            "failed_deliveries": self.anomaly_intake.failed_count,
            "active_anomaly": (
                self.anomaly_intake.active_anomaly.to_dict()
                if self.anomaly_intake.active_anomaly is not None
                else None
            ),
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

    def _specialists(self) -> list[SpecialistAgent]:
        return [agent for agent in self.agents if isinstance(agent, SpecialistAgent)]

    def _specialist_by_role(self, role: str) -> SpecialistAgent:
        normalized = role.strip().lower()
        specialist = next(
            (agent for agent in self._specialists() if agent.role == normalized),
            None,
        )
        if specialist is None:
            raise RuntimeError(f"Specialist SPADE-LLM agent is not available: {normalized}")
        return specialist

    async def _run_communication_probe(self) -> dict[str, Any]:
        technical_lead = self._technical_lead()
        system_engineer = self._specialist_by_role("system_engineer")

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
