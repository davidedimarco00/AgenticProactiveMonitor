from __future__ import annotations

import logging
from typing import Any

from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.template import Template
from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from ..reasoning import (
    AgentSpeakBDIRuntime,
    BDISpecialistTaskResult,
    ReActInvestigationResult,
    SpecialistReActExecutor,
)
from .base import BaseAgent
from .commands import SpecialistTaskAssignment
from .messages import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    parse_spade_message,
)


LOGGER = logging.getLogger("agentic_system.agents.specialist")
HEALTH_PROBE_MESSAGE_TYPE = "runtime_connectivity_probe"
INVESTIGATION_TASK_MESSAGE_TYPE = "investigation_task_assignment"
INVESTIGATION_TASK_ACCEPTED_TYPE = "investigation_task_accepted"
INVESTIGATION_TASK_FAILED_TYPE = "investigation_task_rejected"
INVESTIGATION_TASK_RESULT_TYPE = "investigation_task_result"
INVESTIGATION_TASK_EXECUTION_FAILED_TYPE = "investigation_task_execution_failed"


class SpecialistAgent(BaseAgent):
    """Shared BDI + ReAct base for the four specialist SPADE-LLM agents."""

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
                        anomaly=assignment.anomaly,
                    )
                    self.agent._completed_react_results[assignment.task_id] = result
                    self.agent.tasks_react_completed += 1
                    self.agent.last_react_result = result
                    self.agent.last_react_error = None
                else:
                    result = cached

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
                LOGGER.warning(
                    "%s completed ReAct task=%s incident=%s steps=%d tools=%s confidence=%.3f",
                    self.agent.display_name,
                    assignment.task_id,
                    assignment.incident_id,
                    result.react_steps,
                    ",".join(result.tools_used) or "none",
                    result.confidence,
                )
            except Exception as exc:
                self.agent.last_react_error = str(exc)
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

                # Duplicate delivery is expected with at-least-once dispatch.
                # BDI acceptance is idempotent and ReAct execution is guarded by
                # task_id so a retry cannot start two investigations locally.
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
        provider: LLMProvider,
        mcp_servers: list[MCPServerConfig],
        interaction_memory_path: str,
        agentspeak_specialist_asl: str,
        agentspeak_action_timeout_seconds: float,
        agentspeak_bdi_max_concurrency: int,
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
        self.tasks_accepted = 0
        self.tasks_react_completed = 0
        self.last_received_request: AgentMessage | None = None
        self.last_request_error: str | None = None
        self.last_task_assignment: SpecialistTaskAssignment | None = None
        self.last_bdi_task_result: BDISpecialistTaskResult | None = None
        self.last_react_result: ReActInvestigationResult | None = None
        self.last_react_error: str | None = None

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
            context=self.context,
            tools=list(self.tools),
            max_steps=self._react_max_steps,
            tool_timeout_seconds=self._react_tool_timeout_seconds,
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

    def health_snapshot(self) -> dict[str, object]:
        snapshot = super().health_snapshot()
        bdi = self.last_bdi_task_result
        react = self.last_react_result
        snapshot.update(
            {
                "tasks_accepted": self.tasks_accepted,
                "tasks_react_completed": self.tasks_react_completed,
                "react_inflight": len(self._react_tasks_inflight),
                "react_max_steps": self._react_max_steps,
                "react_tool_timeout_seconds": self._react_tool_timeout_seconds,
                "last_request_error": self.last_request_error,
                "last_react_error": self.last_react_error,
                "last_task_id": (
                    self.last_task_assignment.task_id
                    if self.last_task_assignment is not None
                    else None
                ),
                "specialist_bdi": (
                    {
                        "task_id": bdi.task_id,
                        "incident_id": bdi.incident_id,
                        "goal": bdi.goal,
                        "acceptance_intention": bdi.acceptance_intention,
                        "investigation_intention": bdi.investigation_intention,
                    }
                    if bdi is not None
                    else None
                ),
                "specialist_react": (
                    {
                        "task_id": react.task_id,
                        "incident_id": react.incident_id,
                        "summary": react.summary,
                        "confidence": react.confidence,
                        "react_steps": react.react_steps,
                        "tools_used": list(react.tools_used),
                        "assistance_required": react.assistance_required,
                        "assistance_domain": react.assistance_domain,
                    }
                    if react is not None
                    else None
                ),
            }
        )
        return snapshot
