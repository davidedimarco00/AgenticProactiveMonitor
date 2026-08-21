from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from agentic_system.incidents import (
    InvestigationTaskResultReceipt,
    ReActIncidentCoordinator,
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
    pass


class FakeWorkflow:
    async def mark_operator_action_required(self, *args, **kwargs):
        raise AssertionError("Operator escalation was not expected")


def _coordinator(repository: FakeRepository) -> ReActIncidentCoordinator:
    return ReActIncidentCoordinator(
        FakeWorkflow(),  # type: ignore[arg-type]
        FakeAssignee(),  # type: ignore[arg-type]
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


def test_successful_react_result_completes_task_but_keeps_incident_non_terminal() -> None:
    repository = FakeRepository()
    coordinator = _coordinator(repository)
    receipt = _success_receipt()

    asyncio.run(
        coordinator._persist_successful_react_result(
            deepcopy(repository.incident),
            deepcopy(repository.task),
            receipt,
        )
    )

    assert repository.task["state"] == "COMPLETED"
    assert repository.incident["status"] == "UNDER_ANALYSIS"
    assert repository.incident["agentic"]["current_agent"] == "technical_lead"
    assert repository.incident["agentic"]["active_agents"] == ["technical_lead"]
    assert repository.events[-1]["event_type"] == "SPECIALIST_INVESTIGATION_COMPLETED"
    assert repository.events[-1]["outcome"]["confidence"] == 0.86
    assert repository.events[-1]["outcome"]["evidence"][0]["tool"] == "get_system_load"
    assert coordinator.react_results_completed_count == 1


def test_failed_react_result_moves_task_to_retrying_without_terminal_incident() -> None:
    repository = FakeRepository()
    coordinator = _coordinator(repository)
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
