import asyncio
from copy import deepcopy
from typing import Any

import pytest

from agentic_system.incidents import (
    AgentTaskState,
    AgentTaskWorkflow,
    InvalidTaskTransition,
    validate_task_transition,
)


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self._sequence = 0

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        for task in self.tasks.values():
            if task["idempotency_key"] == payload["idempotency_key"]:
                return deepcopy(task)
        self._sequence += 1
        task = deepcopy(payload)
        task["task_id"] = f"TASK-{self._sequence:03d}"
        self.tasks[task["task_id"]] = task
        return deepcopy(task)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.tasks.get(task_id)
        return deepcopy(task) if task else None

    async def list_tasks(
        self,
        *,
        states: list[str] | None = None,
        incident_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        tasks = list(self.tasks.values())
        if states:
            tasks = [task for task in tasks if task["state"] in states]
        if incident_id:
            tasks = [task for task in tasks if task["incident_id"] == incident_id]
        return deepcopy(tasks[:limit])

    async def transition_task(
        self,
        task_id: str,
        *,
        expected_states: list[str],
        new_state: str,
        patch: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any] | None:
        task = self.tasks.get(task_id)
        if task is None or task["state"] not in expected_states:
            return None
        task["state"] = new_state
        task.update(deepcopy(patch or {}))
        if increment_attempt:
            task["attempt"] = int(task.get("attempt") or 0) + 1
        return deepcopy(task)


def _incident() -> dict[str, Any]:
    return {"incident_id": "INC-20260819-001", "status": "TRIAGED"}


def test_valid_task_lifecycle_reaches_completed_without_failing_agent() -> None:
    repository = FakeTaskRepository()
    workflow = AgentTaskWorkflow(repository, default_max_attempts=3)

    async def scenario() -> dict[str, Any]:
        task = await workflow.create_investigation_task(
            _incident(),
            primary_investigator="network_engineer",
        )
        task = await workflow.mark_dispatched(task["task_id"])
        assert task["attempt"] == 1
        task = await workflow.mark_running(task["task_id"])
        return await workflow.mark_completed(
            task["task_id"],
            outcome={"status": "diagnosed", "summary": "Network latency isolated."},
        )

    completed = asyncio.run(scenario())
    assert completed["state"] == AgentTaskState.COMPLETED.value
    assert completed["attempt"] == 1
    assert completed["last_error"] is None


def test_retryable_failure_moves_task_to_retrying_and_increments_on_redispatch() -> None:
    repository = FakeTaskRepository()
    workflow = AgentTaskWorkflow(repository, default_max_attempts=3)

    async def scenario() -> dict[str, Any]:
        task = await workflow.create_investigation_task(
            _incident(),
            primary_investigator="system_engineer",
        )
        task = await workflow.mark_dispatched(task["task_id"])
        task = await workflow.mark_running(task["task_id"])
        task = await workflow.mark_execution_failed(
            task["task_id"],
            error_type="mcp_timeout",
            message="MCP tool call timed out.",
            retryable=True,
        )
        assert task["state"] == "RETRYING"
        assert task["last_error"]["retryable"] is True
        return await workflow.mark_dispatched(task["task_id"])

    redispatched = asyncio.run(scenario())
    assert redispatched["state"] == "DISPATCHED"
    assert redispatched["attempt"] == 2
    assert redispatched["last_error"] is None


def test_retry_budget_exhaustion_moves_task_to_failed() -> None:
    repository = FakeTaskRepository()
    workflow = AgentTaskWorkflow(repository, default_max_attempts=1)

    async def scenario() -> dict[str, Any]:
        task = await workflow.create_investigation_task(
            _incident(),
            primary_investigator="application_engineer",
        )
        task = await workflow.mark_dispatched(task["task_id"])
        task = await workflow.mark_running(task["task_id"])
        return await workflow.mark_execution_failed(
            task["task_id"],
            error_type="ollama_unavailable",
            message="Reasoning provider unavailable.",
            retryable=True,
        )

    failed = asyncio.run(scenario())
    assert failed["state"] == "FAILED"
    assert failed["attempt"] == 1
    assert failed["last_error"]["retryable"] is False


def test_restart_recovery_reclassifies_interrupted_work() -> None:
    repository = FakeTaskRepository()
    workflow = AgentTaskWorkflow(repository, default_max_attempts=2)

    async def scenario() -> tuple[dict[str, int], dict[str, Any]]:
        task = await workflow.create_investigation_task(
            _incident(),
            primary_investigator="software_developer",
        )
        task = await workflow.mark_dispatched(task["task_id"])
        task = await workflow.mark_running(task["task_id"])
        summary = await workflow.recover_incomplete_tasks()
        recovered = await repository.get_task(task["task_id"])
        assert recovered is not None
        return summary.to_dict(), recovered

    summary, recovered = asyncio.run(scenario())
    assert summary == {"scanned": 1, "retrying": 1, "failed": 0}
    assert recovered["state"] == "RETRYING"
    assert recovered["last_error"]["type"] == "backend_restart"


def test_task_creation_is_idempotent_for_same_incident_and_investigator() -> None:
    repository = FakeTaskRepository()
    workflow = AgentTaskWorkflow(repository)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        first = await workflow.create_investigation_task(
            _incident(),
            primary_investigator="network_engineer",
        )
        second = await workflow.create_investigation_task(
            _incident(),
            primary_investigator="network_engineer",
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first["task_id"] == second["task_id"]
    assert len(repository.tasks) == 1


def test_illegal_transition_is_rejected_before_persistence() -> None:
    with pytest.raises(InvalidTaskTransition):
        validate_task_transition("PENDING", "COMPLETED")
