from __future__ import annotations

import asyncio
import logging
from typing import Any

from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.template import Template
from spade_llm.mcp import MCPServerConfig
from spade_llm.providers.base_provider import BaseLLMProvider

from ..reasoning import (
    AgentSpeakBDIRuntime,
    BDISpecialistTaskResult,
    ReActInvestigationResult,
    SpecialistReActExecutor,
)
from .base import BaseAgent
from .collaboration import (
    PEER_HELP_REQUEST_TYPE,
    PEER_HELP_RESPONSE_TYPE,
    PeerHelpRequestBehaviour,
    PeerHelpResponseBehaviour,
    request_peer_help,
)
from .commands import SpecialistTaskAssignment
from .messages import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    parse_spade_message,
)
from .peer_help import DOMAIN_ROLE, PeerHelpReasoner


LOGGER = logging.getLogger("agentic_system.agents.specialist")
PEER_HELP_RESPONSE_TIMEOUT_SECONDS_DEFAULT = 180.0
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"
INVESTIGATION_TASK_MESSAGE_TYPE = "investigation_task_assignment"
INVESTIGATION_TASK_ACCEPTED_TYPE = "investigation_task_accepted"
INVESTIGATION_TASK_FAILED_TYPE = "investigation_task_rejected"
INVESTIGATION_TASK_RESULT_TYPE = "investigation_task_result"
INVESTIGATION_TASK_EXECUTION_FAILED_TYPE = "investigation_task_execution_failed"


class SpecialistAgent(BaseAgent):
    """Shared BDI + hybrid ReAct base for the four specialist SPADE agents."""

    class HealthProbeRequestBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            try:
                request = parse_spade_message(message)
            except (ValueError, TypeError) as exc:
                self.agent.last_request_error = str(exc)
                LOGGER.warning(
                    "%s received invalid health REQUEST: %s",
                    self.agent.display_name,
                    exc,
                )
                return

            try:
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
                self.agent.last_request_error = None

                LOGGER.info(
                    "%s acknowledged health REQUEST from %s correlation_id=%s",
                    self.agent.display_name,
                    request.sender,
                    request.correlation_id,
                )
            except Exception as exc:
                self.agent.last_request_error = str(exc)
                self.agent.mark_communication_failed()
                LOGGER.exception(
                    "%s failed to process health REQUEST but remains operational: %s",
                    self.agent.display_name,
                    exc,
                )

    class InvestigationReActBehaviour(OneShotBehaviour):
        """Operationally execute the BDI intention without blocking control messages."""

        def __init__(
            self,
            assignment: SpecialistTaskAssignment,
            *,
            receiver: str,
            correlation_id: str,
        ) -> None:
            super().__init__()
            self.assignment = assignment
            self.receiver = receiver
            self.correlation_id = correlation_id

        async def run(self) -> None:
            assignment = self.assignment
            try:
                cached = self.agent._completed_react_results.get(assignment.task_id)
                if cached is None:
                    executor = self.agent._react_executor
                    if executor is None:
                        raise RuntimeError("Specialist ReAct executor was not initialized")

                    self.agent.set_activity(
                        "WORKING",
                        incident_id=assignment.incident_id,
                        detail="react_investigation",
                    )
                    result = await executor.investigate(
                        task_id=assignment.task_id,
                        incident_id=assignment.incident_id,
                        agent_role=self.agent.role,
                        severity=assignment.severity,
                        entity=assignment.entity,
                        anomaly=dict(assignment.anomaly),
                    )
                    self.agent._completed_react_results[assignment.task_id] = result
                    self.agent.tasks_react_completed += 1
                    self.agent.last_react_result = result
                    self.agent.last_react_error = None
                else:
                    result = cached

                # Autonomous peer collaboration: the specialist decides on its
                # own whether one peer domain should also investigate, contacts
                # that peer directly, and folds the evidence into one combined
                # result. The Technical Lead is not involved in this step.
                result = await self.agent._consult_peer_if_needed(assignment, result)

                envelope = AgentMessage.create(
                    type=INVESTIGATION_TASK_RESULT_TYPE,
                    sender=str(self.agent.jid),
                    receiver=self.receiver,
                    correlation_id=self.correlation_id,
                    payload=result.to_payload(),
                )
                await self.agent.send_agent_message(
                    envelope,
                    performative=Performative.INFORM,
                )
                self.agent.set_activity(
                    "WAITING",
                    incident_id=assignment.incident_id,
                    detail="awaiting_technical_lead_review",
                )
                consultation = result.peer_consultation or {}
                LOGGER.warning(
                    "%s completed ReAct task=%s incident=%s steps=%d tools=%s confidence=%.3f peer_consult=%s",
                    self.agent.display_name,
                    assignment.task_id,
                    assignment.incident_id,
                    result.react_steps,
                    ",".join(result.tools_used) or "none",
                    result.confidence,
                    f"{consultation.get('target_role')}:{consultation.get('status')}"
                    if consultation
                    else "none",
                )
            except Exception as exc:
                self.agent.last_react_error = str(exc)
                self.agent.record_trace(
                    {
                        "action": "react_failed",
                        "reason": "Hybrid specialist execution failed; durable task retry policy decides whether to retry.",
                        "incident_id": assignment.incident_id,
                        "task_id": assignment.task_id,
                        "outcome": str(exc),
                    }
                )
                self.agent.set_activity(
                    "IDLE",
                    incident_id=assignment.incident_id,
                    detail="react_investigation_failed",
                )
                failure = AgentMessage.create(
                    type=INVESTIGATION_TASK_EXECUTION_FAILED_TYPE,
                    sender=str(self.agent.jid),
                    receiver=self.receiver,
                    correlation_id=self.correlation_id,
                    payload={
                        "accepted_by": self.agent.role,
                        "task_id": assignment.task_id,
                        "incident_id": assignment.incident_id,
                        "error": str(exc),
                        "retryable": True,
                    },
                )
                try:
                    await self.agent.send_agent_message(
                        failure,
                        performative=Performative.FAILURE,
                    )
                except Exception:
                    LOGGER.exception(
                        "%s could not report ReAct failure for task=%s",
                        self.agent.display_name,
                        assignment.task_id,
                    )
                LOGGER.exception(
                    "%s ReAct investigation failed without changing agent health: %s",
                    self.agent.display_name,
                    exc,
                )
            finally:
                self.agent._react_tasks_inflight.discard(assignment.task_id)

    class InvestigationTaskBehaviour(CyclicBehaviour):
        """Accept Technical Lead delegation and commit a local BDI intention."""

        async def run(self) -> None:
            message = await self.receive(timeout=1)
            if message is None:
                return

            request: AgentMessage | None = None
            assignment: SpecialistTaskAssignment | None = None
            try:
                request = parse_spade_message(message)
                self.agent.mark_message_received()
                self.agent.last_received_request = request
                assignment = SpecialistTaskAssignment.from_payload(request.payload)

                if assignment.assigned_to != self.agent.role:
                    raise RuntimeError(
                        f"Task {assignment.task_id} targets {assignment.assigned_to}, "
                        f"not {self.agent.role}"
                    )

                deliberation = self.agent._accepted_tasks.get(assignment.task_id)
                if deliberation is None:
                    self.agent.set_activity(
                        "WORKING",
                        incident_id=assignment.incident_id,
                        detail="specialist_bdi_deliberation",
                    )
                    deliberation = await self.agent._bdi_runtime.accept_specialist_task(
                        task_id=assignment.task_id,
                        incident_id=assignment.incident_id,
                        role=self.agent.role,
                        task_type=assignment.task_type,
                    )
                    self.agent._accepted_tasks[assignment.task_id] = deliberation
                    self.agent.tasks_accepted += 1

                self.agent.last_task_assignment = assignment
                self.agent.last_bdi_task_result = deliberation
                self.agent.last_request_error = None
                self.agent.record_trace(
                    {
                        "action": "bdi_intention",
                        "reason": (
                            f"AgentSpeak accepted goal {deliberation.goal} and committed "
                            f"intention {deliberation.investigation_intention}."
                        ),
                        "incident_id": assignment.incident_id,
                        "task_id": assignment.task_id,
                        "outcome": f"acceptance={deliberation.acceptance_intention}",
                    }
                )
                self.agent.set_activity(
                    "WORKING",
                    incident_id=assignment.incident_id,
                    detail="investigation_intention_committed",
                )

                acknowledgement = AgentMessage.create(
                    type=INVESTIGATION_TASK_ACCEPTED_TYPE,
                    sender=str(self.agent.jid),
                    receiver=request.sender,
                    correlation_id=request.correlation_id,
                    payload={
                        "accepted_by": self.agent.role,
                        "task_id": assignment.task_id,
                        "incident_id": assignment.incident_id,
                        "attempt": assignment.attempt,
                        "bdi_goal": deliberation.goal,
                        "bdi_acceptance_intention": deliberation.acceptance_intention,
                        "bdi_investigation_intention": deliberation.investigation_intention,
                    },
                )
                await self.agent.send_agent_message(
                    acknowledgement,
                    performative=Performative.AGREE,
                )
                self.agent.start_react_investigation(
                    assignment,
                    receiver=request.sender,
                    correlation_id=request.correlation_id,
                )
                LOGGER.warning(
                    "%s accepted task=%s incident=%s through BDI goal=%s intention=%s; ReAct scheduled",
                    self.agent.display_name,
                    assignment.task_id,
                    assignment.incident_id,
                    deliberation.goal,
                    deliberation.investigation_intention,
                )
            except Exception as exc:
                self.agent.last_request_error = str(exc)
                incident_id = assignment.incident_id if assignment is not None else None
                self.agent.set_activity(
                    "IDLE",
                    incident_id=incident_id,
                    detail="task_acceptance_failed",
                )
                LOGGER.exception(
                    "%s rejected delegated investigation task but remains operational: %s",
                    self.agent.display_name,
                    exc,
                )
                if request is not None:
                    failure = AgentMessage.create(
                        type=INVESTIGATION_TASK_FAILED_TYPE,
                        sender=str(self.agent.jid),
                        receiver=request.sender,
                        correlation_id=request.correlation_id,
                        payload={
                            "accepted_by": self.agent.role,
                            "task_id": (
                                assignment.task_id if assignment is not None else None
                            ),
                            "error": str(exc),
                        },
                    )
                    try:
                        await self.agent.send_agent_message(
                            failure,
                            performative=Performative.FAILURE,
                        )
                    except Exception:
                        LOGGER.exception(
                            "%s could not send task failure response",
                            self.agent.display_name,
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
        provider: BaseLLMProvider,
        tool_provider: BaseLLMProvider,
        mcp_servers: list[MCPServerConfig],
        interaction_memory_path: str,
        agentspeak_specialist_asl: str,
        agentspeak_action_timeout_seconds: float,
        agentspeak_bdi_max_concurrency: int,
        peer_help_enabled: bool = True,
        peer_help_response_timeout_seconds: float = PEER_HELP_RESPONSE_TIMEOUT_SECONDS_DEFAULT,
    ) -> None:
        super().__init__(
            jid,
            password,
            role=role,
            display_name=display_name,
            health_port=health_port,
            provider=provider,  # type: ignore[arg-type]
            mcp_servers=mcp_servers,
            system_prompt=system_prompt,
            interaction_memory_path=interaction_memory_path,
        )
        self._tool_provider = tool_provider
        self.set_model_roles(
            reasoning=str(provider.model),
            tool_selection=str(tool_provider.model),
        )
        self._bdi_runtime = AgentSpeakBDIRuntime(
            specialist_asl=agentspeak_specialist_asl,
            action_timeout_seconds=agentspeak_action_timeout_seconds,
            max_concurrency=agentspeak_bdi_max_concurrency,
        )
        self._accepted_tasks: dict[str, BDISpecialistTaskResult] = {}
        self._completed_react_results: dict[str, ReActInvestigationResult] = {}
        self._react_tasks_inflight: set[str] = set()
        self._react_executor: SpecialistReActExecutor | None = None
        self._react_max_steps = 6
        self._react_tool_timeout_seconds = 30.0
        if peer_help_response_timeout_seconds <= 0:
            raise ValueError("peer_help_response_timeout_seconds must be greater than zero")
        self._peer_help_enabled = bool(peer_help_enabled)
        self._peer_help_response_timeout_seconds = float(peer_help_response_timeout_seconds)
        self._peer_help_reasoner = PeerHelpReasoner(role, provider)
        self._peer_directory: dict[str, str] = {}
        self._pending_peer_help: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._peer_help_by_incident: set[str] = set()
        self.tasks_accepted = 0
        self.tasks_react_completed = 0
        self.peer_help_requests_sent = 0
        self.peer_help_requests_served = 0
        self.peer_help_timeouts = 0
        self.last_received_request: AgentMessage | None = None
        self.last_request_error: str | None = None
        self.last_task_assignment: SpecialistTaskAssignment | None = None
        self.last_bdi_task_result: BDISpecialistTaskResult | None = None
        self.last_react_result: ReActInvestigationResult | None = None
        self.last_react_error: str | None = None
        self.last_peer_consultation: dict[str, Any] | None = None

    def configure_react(
        self,
        *,
        max_steps: int,
        tool_timeout_seconds: float,
    ) -> None:
        if self.lifecycle_state != "created":
            raise RuntimeError("ReAct must be configured before the specialist starts")
        if max_steps <= 0 or tool_timeout_seconds <= 0:
            raise ValueError("ReAct limits must be greater than zero")
        self._react_max_steps = max_steps
        self._react_tool_timeout_seconds = tool_timeout_seconds

    async def setup(self) -> None:
        await super().setup()

        self._react_executor = SpecialistReActExecutor(
            provider=self.provider,
            tool_provider=self._tool_provider,
            context=self.context,
            tools=list(self.tools),
            max_steps=self._react_max_steps,
            tool_timeout_seconds=self._react_tool_timeout_seconds,
            trace_sink=self.record_trace,
        )

        health_template = Template()
        health_template.set_metadata("protocol", AGENTIC_PROTOCOL)
        health_template.set_metadata("performative", Performative.REQUEST.value)
        health_template.set_metadata("message_type", HEALTH_PROBE_MESSAGE_TYPE)
        self.add_behaviour(self.HealthProbeRequestBehaviour(), health_template)

        task_template = Template()
        task_template.set_metadata("protocol", AGENTIC_PROTOCOL)
        task_template.set_metadata("performative", Performative.REQUEST.value)
        task_template.set_metadata("message_type", INVESTIGATION_TASK_MESSAGE_TYPE)
        self.add_behaviour(self.InvestigationTaskBehaviour(), task_template)

        peer_help_request_template = Template()
        peer_help_request_template.set_metadata("protocol", AGENTIC_PROTOCOL)
        peer_help_request_template.set_metadata("performative", Performative.REQUEST.value)
        peer_help_request_template.set_metadata("message_type", PEER_HELP_REQUEST_TYPE)
        self.add_behaviour(PeerHelpRequestBehaviour(), peer_help_request_template)

        for performative in (Performative.INFORM, Performative.FAILURE):
            peer_help_response_template = Template()
            peer_help_response_template.set_metadata("protocol", AGENTIC_PROTOCOL)
            peer_help_response_template.set_metadata("performative", performative.value)
            peer_help_response_template.set_metadata("message_type", PEER_HELP_RESPONSE_TYPE)
            self.add_behaviour(PeerHelpResponseBehaviour(), peer_help_response_template)

    def set_peer_directory(self, directory: dict[str, str]) -> None:
        """Address book of role -> jid, populated by the runtime after connect."""

        self._peer_directory = {
            str(role).strip().lower(): str(jid)
            for role, jid in directory.items()
            if str(role).strip().lower() != self.role and str(jid).strip()
        }

    async def _consult_peer_if_needed(
        self,
        assignment: SpecialistTaskAssignment,
        result: ReActInvestigationResult,
    ) -> ReActInvestigationResult:
        """Decide with the specialist's own LLM whether to consult one peer, then do so."""

        incident_id = assignment.incident_id
        if not self._peer_help_enabled:
            return result
        if not result.assistance_required:
            return result
        if str(assignment.task_id).startswith("peer-help:"):
            return result
        if incident_id in self._peer_help_by_incident:
            return result

        self._peer_help_by_incident.add(incident_id)
        consultation: dict[str, Any] = {
            "requested": False,
            "target_role": None,
            "reason": "",
            "status": "skipped",
        }
        try:
            decision = await self._peer_help_reasoner.assess(
                incident_id=incident_id,
                result=result.to_payload(),
            )
            if not decision.needs_help or decision.target_domain is None:
                consultation["status"] = "skipped"
                return self._attach_consultation(result, consultation)

            target_role = DOMAIN_ROLE[decision.target_domain]
            peer_jid = self._peer_directory.get(target_role)
            consultation.update(
                {
                    "requested": True,
                    "target_role": target_role,
                    "reason": decision.reason,
                }
            )
            if not peer_jid:
                consultation["status"] = "unavailable"
                LOGGER.warning(
                    "%s wanted peer help from %s for incident=%s but has no address",
                    self.display_name,
                    target_role,
                    incident_id,
                )
                return self._attach_consultation(result, consultation)

            self.peer_help_requests_sent += 1
            self.set_activity(
                "WORKING",
                incident_id=incident_id,
                detail=f"consulting_{target_role}",
            )
            LOGGER.warning(
                "%s consulting peer %s for incident=%s: %s",
                self.display_name,
                target_role,
                incident_id,
                decision.reason,
            )
            peer_payload = await request_peer_help(
                self,
                receiver=peer_jid,
                incident_id=incident_id,
                requester_role=self.role,
                reason=decision.reason,
                requester_result=result.to_payload(),
                severity=assignment.severity,
                entity=assignment.entity,
                anomaly=dict(assignment.anomaly),
                timeout=self._peer_help_response_timeout_seconds,
            )
            combined = self._react_executor.finalize_with_peer_help(
                cached_result=result,
                peer_result=peer_payload,
                peer_role=target_role,
                reason=decision.reason,
            )
            self._completed_react_results[assignment.task_id] = combined
            self.last_peer_consultation = combined.peer_consultation
            self.last_react_result = combined
            LOGGER.warning(
                "%s folded peer %s evidence into incident=%s combined confidence=%.3f",
                self.display_name,
                target_role,
                incident_id,
                combined.confidence,
            )
            return combined
        except (TimeoutError, asyncio.TimeoutError):
            self.peer_help_timeouts += 1
            consultation["status"] = "unavailable"
            LOGGER.warning(
                "%s peer help timed out for incident=%s target=%s",
                self.display_name,
                incident_id,
                consultation.get("target_role"),
            )
            return self._attach_consultation(result, consultation)
        except Exception as exc:  # noqa: BLE001 - best-effort: keep the solo result
            consultation["status"] = "unavailable"
            LOGGER.exception(
                "%s peer consultation failed for incident=%s, keeping solo result: %s",
                self.display_name,
                incident_id,
                exc,
            )
            return self._attach_consultation(result, consultation)

    def _attach_consultation(
        self,
        result: ReActInvestigationResult,
        consultation: dict[str, Any],
    ) -> ReActInvestigationResult:
        from dataclasses import replace

        self.last_peer_consultation = dict(consultation)
        # A consultation was attempted for this incident; do not ask again.
        updated = replace(
            result,
            assistance_required=False,
            assistance_domain=None,
            peer_consultation=dict(consultation),
        )
        return updated

    def start_react_investigation(
        self,
        assignment: SpecialistTaskAssignment,
        *,
        receiver: str,
        correlation_id: str,
    ) -> None:
        if assignment.task_id in self._react_tasks_inflight:
            return
        self._react_tasks_inflight.add(assignment.task_id)
        self.add_behaviour(
            self.InvestigationReActBehaviour(
                assignment,
                receiver=receiver,
                correlation_id=correlation_id,
            )
        )

    def health_snapshot(self) -> dict[str, Any]:
        snapshot = super().health_snapshot()
        snapshot.update(
            {
                "tasks_accepted": self.tasks_accepted,
                "tasks_react_completed": self.tasks_react_completed,
                "peer_help_enabled": self._peer_help_enabled,
                "peer_help_requests_sent": self.peer_help_requests_sent,
                "peer_help_requests_served": self.peer_help_requests_served,
                "peer_help_timeouts": self.peer_help_timeouts,
                "peer_directory_roles": sorted(self._peer_directory),
                "last_peer_consultation": self.last_peer_consultation,
                "last_react_error": self.last_react_error,
            }
        )
        return snapshot
