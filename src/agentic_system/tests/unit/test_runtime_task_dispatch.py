from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agentic_system.agents.messages import AgentMessage
from agentic_system.agents.specialist import (
    INVESTIGATION_TASK_ACCEPTED_TYPE,
    INVESTIGATION_TASK_MESSAGE_TYPE,
)
from agentic_system.runtime import AgentRuntime


class FakeTechnicalLead:
    role = "technical_lead"
    jid = "technical-lead@xmpp"

    def __init__(self) -> None:
        self.last_request_type: str | None = None
        self.last_payload: dict[str, Any] | None = None

    async def request_specialist(
        self,
        *,
        receiver: str,
        request_type: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> tuple[AgentMessage, AgentMessage]:
        del timeout
        self.last_request_type = request_type
        self.last_payload = dict(payload or {})
        request = AgentMessage.create(
            type=request_type,
            sender=self.jid,
            receiver=receiver,
            payload=payload,
            correlation_id="corr-dispatch-001",
        )
        acknowledgement = AgentMessage.create(
            type=INVESTIGATION_TASK_ACCEPTED_TYPE,
            sender=receiver,
            receiver=self.jid,
            correlation_id=request.correlation_id,
            payload={
                "accepted_by": "network_engineer",
                "task_id": "TASK-001",
                "incident_id": "INC-001",
                "bdi_goal": "handle_investigation_task",
                "bdi_acceptance_intention": "accept_task",
                "bdi_investigation_intention": "investigate_incident",
            },
        )
        return request, acknowledgement


class FakeSpecialist:
    role = "network_engineer"
    jid = "network-engineer@xmpp"
    xmpp_connected = True
    communication_ok = True

    def mark_communication_ok(self) -> None:
        self.communication_ok = True

    def mark_communication_failed(self) -> None:
        self.communication_ok = False


def test_runtime_dispatches_to_selected_specialist_and_returns_bdi_receipt() -> None:
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.started = True
    runtime.config = SimpleNamespace(task_dispatch_timeout_seconds=10.0)
    technical_lead = FakeTechnicalLead()
    specialist = FakeSpecialist()
    runtime._technical_lead = lambda: technical_lead  # type: ignore[method-assign]
    runtime._specialist_by_role = lambda role: specialist  # type: ignore[method-assign]

    receipt = asyncio.run(
        runtime.dispatch_investigation_task(
            {
                "incident_id": "INC-001",
                "severity": "HIGH",
                "entity": "api-gateway",
                "anomaly": {"detector_id": "NETLAT-api-gateway"},
            },
            {
                "task_id": "TASK-001",
                "incident_id": "INC-001",
                "task_type": "INVESTIGATE_INCIDENT",
                "assigned_to": "network_engineer",
                "state": "DISPATCHED",
                "attempt": 1,
                "max_attempts": 3,
            },
        )
    )

    assert technical_lead.last_request_type == INVESTIGATION_TASK_MESSAGE_TYPE
    assert technical_lead.last_payload is not None
    assert technical_lead.last_payload["assigned_to"] == "network_engineer"
    assert technical_lead.last_payload["attempt"] == 1
    assert receipt.task_id == "TASK-001"
    assert receipt.agent_role == "network_engineer"
    assert receipt.correlation_id == "corr-dispatch-001"
    assert receipt.bdi_goal == "handle_investigation_task"
    assert receipt.bdi_investigation_intention == "investigate_incident"
