from __future__ import annotations

import asyncio

from agentic_system.agents.messages import AgentMessage
from agentic_system.runtime import AgentRuntime
import agentic_system.runtime as runtime_module


class FakeAgent:
    def __init__(self, jid: str) -> None:
        self.jid = jid


def test_runtime_preserves_diagnostic_closure_from_xmpp_payload(monkeypatch) -> None:
    runtime = object.__new__(AgentRuntime)
    technical_lead = FakeAgent("technical-lead@xmpp")
    specialist = FakeAgent("application-engineer@xmpp")
    runtime._technical_lead = lambda: technical_lead  # type: ignore[method-assign]
    runtime._specialist_by_role = lambda role: specialist  # type: ignore[method-assign]

    message = AgentMessage.create(
        type="investigation_task_result",
        sender="application-engineer@xmpp",
        receiver="technical-lead@xmpp",
        correlation_id="corr-diagnostic-001",
        payload={
            "task_id": "TASK-DIAG-001",
            "incident_id": "INC-DIAG-001",
            "agent_role": "application_engineer",
            "status": "completed",
            "summary": "The application-level delay explains the observed service latency.",
            "diagnosis_status": "confirmed",
            "root_cause": "processing-service applies an artificial delay before downstream calls.",
            "causal_chain": [
                "processing-service applies the configured delay.",
                "request completion time increases.",
                "the latency anomaly is observed between the services.",
            ],
            "confidence": 0.95,
            "findings": ["Logs report the application delay during the anomaly interval."],
            "evidence": [
                {
                    "step": 3,
                    "tool": "apm_mcp_search_logs",
                    "arguments": {"service": "processing-service"},
                    "observation": {"message": "fault_delay_applied"},
                    "success": True,
                }
            ],
            "hypotheses": [],
            "recommended_next_steps": [],
            "assistance_required": False,
            "assistance_domain": None,
            "react_steps": 7,
            "tools_used": ["apm_mcp_search_logs", "apm_mcp_get_runtime_stats"],
            "conversation_id": "react:application_engineer:INC-DIAG-001:TASK-DIAG-001",
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "pop_investigation_result",
        lambda agent, task_id: message,
    )

    receipt = asyncio.run(
        runtime.collect_investigation_result(
            {
                "incident_id": "INC-DIAG-001",
                "status": "UNDER_ANALYSIS",
            },
            {
                "task_id": "TASK-DIAG-001",
                "incident_id": "INC-DIAG-001",
                "assigned_to": "application_engineer",
            },
        )
    )

    assert receipt is not None
    assert receipt.succeeded is True
    assert receipt.diagnosis_status == "confirmed"
    assert receipt.root_cause == (
        "processing-service applies an artificial delay before downstream calls."
    )
    assert receipt.causal_chain == (
        "processing-service applies the configured delay.",
        "request completion time increases.",
        "the latency anomaly is observed between the services.",
    )
    assert receipt.assistance_required is False
    assert receipt.task_outcome()["diagnosis_status"] == "confirmed"


def test_runtime_accepts_nonconfirmed_result_without_peer_assistance(monkeypatch) -> None:
    runtime = object.__new__(AgentRuntime)
    technical_lead = FakeAgent("technical-lead@xmpp")
    specialist = FakeAgent("network-engineer@xmpp")
    runtime._technical_lead = lambda: technical_lead  # type: ignore[method-assign]
    runtime._specialist_by_role = lambda role: specialist  # type: ignore[method-assign]

    message = AgentMessage.create(
        type="investigation_task_result",
        sender="network-engineer@xmpp",
        receiver="technical-lead@xmpp",
        correlation_id="corr-diagnostic-inconclusive",
        payload={
            "task_id": "TASK-DIAG-INCONCLUSIVE",
            "incident_id": "INC-DIAG-INCONCLUSIVE",
            "agent_role": "network_engineer",
            "summary": "Network evidence does not explain the anomaly.",
            "diagnosis_status": "inconclusive",
            "root_cause": None,
            "causal_chain": [],
            "confidence": 0.35,
            "findings": [],
            "evidence": [],
            "hypotheses": [],
            "recommended_next_steps": ["Escalate unresolved diagnosis for operator review."],
            "assistance_required": False,
            "assistance_domain": None,
            "react_steps": 4,
            "tools_used": ["apm_mcp_get_network_connections"],
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "pop_investigation_result",
        lambda agent, task_id: message,
    )

    receipt = asyncio.run(
        runtime.collect_investigation_result(
            {"incident_id": "INC-DIAG-INCONCLUSIVE"},
            {
                "task_id": "TASK-DIAG-INCONCLUSIVE",
                "incident_id": "INC-DIAG-INCONCLUSIVE",
                "assigned_to": "network_engineer",
            },
        )
    )

    assert receipt is not None
    assert receipt.succeeded is True
    assert receipt.diagnosis_status == "inconclusive"
    assert receipt.root_cause is None
    assert receipt.assistance_required is False
    assert receipt.assistance_domain is None
