from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from agentic_system.incidents import (
    InvestigationTaskResultReceipt,
    ReActIncidentCoordinator,
    TechnicalLeadReviewReceipt,
)


class FakeRepository:
    def __init__(self) -> None:
        self.incident = {
            "incident_id": "INC-REACT-001",
            "status": "TRIAGED",
            "agentic": {
                "investigation_task_id": "TASK-REACT-001",
                "current_agent": "system_engineer",
                "active_agents": ["technical_lead", "system_engineer"],
            },
        }
        self.task = {
            "task_id": "TASK-REACT-001",
            "incident_id": "INC-REACT-001",
            "state": "RUNNING",
            "attempt": 1,
            "max_attempts": 3,
        }
        self.events: list[dict[str, Any]] = []

    async def get_incident(self, incident_id: str):
        return deepcopy(self.incident) if incident_id == self.incident["incident_id"] else None

    async def get_task(self, task_id: str):
        return deepcopy(self.task) if task_id == self.task["task_id"] else None

    async def update_incident(self, incident_id: str, patch: dict[str, Any]):
        if incident_id != self.incident["incident_id"]:
            return None
        for key, value in deepcopy(patch).items():
            if isinstance(value, dict) and isinstance(self.incident.get(key), dict):
                self.incident[key].update(value)
            else:
                self.incident[key] = value
        return deepcopy(self.incident)

    async def add_event(self, incident_id: str, payload: dict[str, Any]):
        event = {"incident_id": incident_id, **deepcopy(payload)}
        self.events.append(event)
        return event


class FakeTaskWorkflow:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository

    async def mark_completed(self, task_id: str, *, outcome=None):
        assert task_id == self.repository.task["task_id"]
        self.repository.task["state"] = "COMPLETED"
        self.repository.task["outcome"] = deepcopy(outcome)
        return deepcopy(self.repository.task)

    async def mark_execution_failed(
        self,
        task_id: str,
        *,
        error_type: str,
        message: str,
        retryable: bool = True,
    ):
        assert task_id == self.repository.task["task_id"]
        self.repository.task["state"] = "RETRYING" if retryable else "FAILED"
        self.repository.task["last_error"] = {
            "type": error_type,
            "message": message,
            "retryable": retryable,
        }
        return deepcopy(self.repository.task)


class FakeAssignee:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.agents: list[Any] = []

    async def review_investigation_result(self, incident, result):
        return TechnicalLeadReviewReceipt(
            incident_id=str(incident["incident_id"]),
            decision=self.decision,
            confidence=0.9,
            diagnosis_summary="Specialist evidence was reviewed by the Technical Lead.",
            root_cause="The available evidence supports a transient resource anomaly.",
            rationale="The critic accepted the evidence gathered by the specialist.",
            remediation_summary="No autonomous corrective change is required.",
            remediation_steps=(),
            support_domain="application" if self.decision == "request_support" else None,
            support_reason=(
                "Application evidence is still required."
                if self.decision == "request_support"
                else None
            ),
            bdi_goal="review_investigation",
            bdi_review_intention="review_specialist_result",
            bdi_decision_intention="commit_review_decision",
        )


class FakeWorkflow:
    async def mark_operator_action_required(self, *args, **kwargs):
        raise AssertionError("Operator escalation was not expected")


def _coordinator(repository: FakeRepository, *, decision: str) -> ReActIncidentCoordinator:
    return ReActIncidentCoordinator(
        FakeWorkflow(),  # type: ignore[arg-type]
        FakeAssignee(decision),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        FakeTaskWorkflow(repository),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
    )


def _success_receipt() -> InvestigationTaskResultReceipt:
    return InvestigationTaskResultReceipt(
        task_id="TASK-REACT-001",
        incident_id="INC-REACT-001",
        agent_role="system_engineer",
        agent_jid="system-engineer@xmpp",
        correlation_id="corr-react-001",
        succeeded=True,
        summary="CPU saturation is supported by live metrics.",
        confidence=0.86,
        findings=("CPU remained above the expected range.",),
        evidence=(
            {
                "step": 1,
                "tool": "get_system_load",
                "arguments": {"service": "processing-service"},
                "observation": {"cpu_percent": 388.2},
                "success": True,
            },
        ),
        hypotheses=("A CPU-bound workload is saturating the service.",),
        recommended_next_steps=("Correlate with application logs.",),
        assistance_required=True,
        assistance_domain="application",
        react_steps=2,
        tools_used=("get_system_load",),
        conversation_id="react:system_engineer:INC-REACT-001:TASK-REACT-001",
        retryable=False,
    )


def test_successful_react_result_can_request_support_after_tl_review() -> None:
    repository = FakeRepository()
    coordinator = _coordinator(repository, decision="request_support")

    asyncio.run(
        coordinator._persist_successful_react_result(
            deepcopy(repository.incident),
            deepcopy(repository.task),
            _success_receipt(),
        )
    )

    assert repository.task["state"] == "COMPLETED"
    assert repository.incident["status"] == "UNDER_ANALYSIS"
    assert repository.incident["agentic"]["review_decision"] == "request_support"
    assert repository.incident["agentic"]["support_requested"] is True
    assert repository.incident["agentic"]["support_domain"] == "application"
    assert repository.events[-1]["event_type"] == "TECHNICAL_LEAD_REVIEW_COMPLETED"
    assert coordinator.react_results_completed_count == 1
    assert coordinator.technical_lead_reviews_completed_count == 1


def test_successful_react_result_can_resolve_incident_after_tl_review() -> None:
    repository = FakeRepository()
    coordinator = _coordinator(repository, decision="resolve")

    asyncio.run(
        coordinator._persist_successful_react_result(
            deepcopy(repository.incident),
            deepcopy(repository.task),
            _success_receipt(),
        )
    )

    assert repository.task["state"] == "COMPLETED"
    assert repository.incident["status"] == "RESOLVED"
    assert repository.incident["agentic"]["active_agents"] == []
    assert repository.incident["diagnosis"]["confidence"] == 0.9
    assert repository.incident["diagnosis"]["evidence"] == [
        "CPU remained above the expected range."
    ]
    assert repository.incident["remediation"]["status"] == "ADVISORY"
    assert repository.incident["validation"]["status"] == "EVIDENCE_REVIEWED"
    assert repository.events[-1]["event_type"] == "TECHNICAL_LEAD_REVIEW_COMPLETED"


def test_failed_react_result_moves_task_to_retrying_without_terminal_incident() -> None:
    repository = FakeRepository()
    coordinator = _coordinator(repository, decision="resolve")
    receipt = InvestigationTaskResultReceipt(
        task_id="TASK-REACT-001",
        incident_id="INC-REACT-001",
        agent_role="system_engineer",
        agent_jid="system-engineer@xmpp",
        correlation_id="corr-react-fail",
        succeeded=False,
        error="MCP request failed",
        retryable=True,
    )

    asyncio.run(
        coordinator._persist_failed_react_result(
            deepcopy(repository.incident),
            deepcopy(repository.task),
            receipt,
        )
    )

    assert repository.task["state"] == "RETRYING"
    assert repository.incident["status"] == "TRIAGED"
    assert repository.events[-1]["event_type"] == "SPECIALIST_INVESTIGATION_FAILED"
    assert repository.events[-1]["outcome"]["retryable"] is True
    assert coordinator.react_results_failed_count == 1
