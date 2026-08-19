from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import Any

from .contracts import AgentTaskRepositoryPort


LOGGER = logging.getLogger("agentic_system.incidents.tasks")
INVESTIGATION_TASK_TYPE = "INVESTIGATE_INCIDENT"


class AgentTaskState(StrEnum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TASK_TRANSITIONS: dict[AgentTaskState, set[AgentTaskState]] = {
    AgentTaskState.PENDING: {
        AgentTaskState.DISPATCHED,
        AgentTaskState.FAILED,
    },
    AgentTaskState.DISPATCHED: {
        AgentTaskState.RUNNING,
        AgentTaskState.RETRYING,
        AgentTaskState.FAILED,
    },
    AgentTaskState.RUNNING: {
        AgentTaskState.COMPLETED,
        AgentTaskState.RETRYING,
        AgentTaskState.FAILED,
    },
    AgentTaskState.RETRYING: {
        AgentTaskState.DISPATCHED,
        AgentTaskState.FAILED,
    },
    AgentTaskState.COMPLETED: set(),
    AgentTaskState.FAILED: set(),
}

RECOVERABLE_TASK_STATES = {
    AgentTaskState.PENDING,
    AgentTaskState.DISPATCHED,
    AgentTaskState.RUNNING,
    AgentTaskState.RETRYING,
}
IN_FLIGHT_TASK_STATES = {
    AgentTaskState.DISPATCHED,
    AgentTaskState.RUNNING,
}
TERMINAL_TASK_STATES = {
    AgentTaskState.COMPLETED,
    AgentTaskState.FAILED,
}


class InvalidTaskTransition(RuntimeError):
    pass


def normalize_task_state(value: str | AgentTaskState) -> AgentTaskState:
    try:
        return AgentTaskState(str(value).strip().upper())
    except ValueError as exc:
        raise InvalidTaskTransition(f"Unsupported agent task state: {value!r}") from exc


def validate_task_transition(
    current: str | AgentTaskState,
    target: str | AgentTaskState,
) -> tuple[AgentTaskState, AgentTaskState]:
    current_state = normalize_task_state(current)
    target_state = normalize_task_state(target)
    if target_state not in TASK_TRANSITIONS[current_state]:
        raise InvalidTaskTransition(
            f"Agent task transition {current_state.value} -> {target_state.value} is not allowed"
        )
    return current_state, target_state


@dataclass(frozen=True, slots=True)
class TaskRecoverySummary:
    scanned: int = 0
    retrying: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "retrying": self.retrying,
            "failed": self.failed,
        }


class AgentTaskWorkflow:
    """Durable state machine for work assigned by the Technical Lead.

    The task is persisted independently from the in-memory SPADE behaviour so a
    failed action does not imply a failed agent. Dispatch and specialist ReAct
    execution are deliberately left to the next workflow stage; this class owns
    persistence, idempotency, retry eligibility and restart recovery.
    """

    def __init__(
        self,
        repository: AgentTaskRepositoryPort,
        *,
        default_max_attempts: int = 3,
    ) -> None:
        if default_max_attempts <= 0:
            raise ValueError("default_max_attempts must be greater than zero")
        self.repository = repository
        self.default_max_attempts = default_max_attempts

    async def create_investigation_task(
        self,
        incident: dict[str, Any],
        *,
        primary_investigator: str,
        created_by: str = "technical_lead",
    ) -> dict[str, Any]:
        incident_id = str(incident.get("incident_id") or "").strip()
        if not incident_id:
            raise ValueError("A persisted incident_id is required to create an agent task")
        investigator = primary_investigator.strip().lower()
        if not investigator:
            raise ValueError("primary_investigator is required")

        # Stable key makes task creation idempotent across retries and restarts.
        idempotency_key = f"{incident_id}:{INVESTIGATION_TASK_TYPE}:{investigator}"
        return await self.repository.create_task(
            {
                "incident_id": incident_id,
                "task_type": INVESTIGATION_TASK_TYPE,
                "created_by": created_by,
                "assigned_to": investigator,
                "state": AgentTaskState.PENDING.value,
                "attempt": 0,
                "max_attempts": self.default_max_attempts,
                "idempotency_key": idempotency_key,
                "last_error": None,
            }
        )

    async def mark_dispatched(self, task_id: str) -> dict[str, Any]:
        task = await self._require_task(task_id)
        state = normalize_task_state(task["state"])
        if state not in {AgentTaskState.PENDING, AgentTaskState.RETRYING}:
            validate_task_transition(state, AgentTaskState.DISPATCHED)

        attempt = int(task.get("attempt") or 0)
        max_attempts = int(task.get("max_attempts") or self.default_max_attempts)
        if attempt >= max_attempts:
            return await self._transition(
                task,
                AgentTaskState.FAILED,
                patch={
                    "last_error": {
                        "type": "retry_exhausted",
                        "message": "Maximum dispatch attempts reached before a new dispatch.",
                        "retryable": False,
                    }
                },
            )

        transitioned = await self.repository.transition_task(
            task_id,
            expected_states=[state.value],
            new_state=AgentTaskState.DISPATCHED.value,
            patch={"last_error": None},
            increment_attempt=True,
        )
        if transitioned is None:
            raise InvalidTaskTransition(
                f"Task {task_id} changed concurrently while being dispatched"
            )
        return transitioned

    async def mark_running(self, task_id: str) -> dict[str, Any]:
        task = await self._require_task(task_id)
        return await self._transition(task, AgentTaskState.RUNNING)

    async def mark_completed(
        self,
        task_id: str,
        *,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = await self._require_task(task_id)
        return await self._transition(
            task,
            AgentTaskState.COMPLETED,
            patch={"outcome": dict(outcome or {}), "last_error": None},
        )

    async def mark_execution_failed(
        self,
        task_id: str,
        *,
        error_type: str,
        message: str,
        retryable: bool = True,
    ) -> dict[str, Any]:
        task = await self._require_task(task_id)
        state = normalize_task_state(task["state"])
        if state in TERMINAL_TASK_STATES:
            return task

        attempt = int(task.get("attempt") or 0)
        max_attempts = int(task.get("max_attempts") or self.default_max_attempts)
        should_retry = retryable and attempt < max_attempts
        target = AgentTaskState.RETRYING if should_retry else AgentTaskState.FAILED
        return await self._transition(
            task,
            target,
            patch={
                "last_error": {
                    "type": error_type.strip() or "task_execution_error",
                    "message": message.strip() or "Agent task execution failed.",
                    "retryable": should_retry,
                }
            },
        )

    async def recover_incomplete_tasks(
        self,
        *,
        incident_id: str | None = None,
    ) -> TaskRecoverySummary:
        """Move interrupted in-flight work to RETRYING or FAILED after restart.

        PENDING and RETRYING are already safe durable states and remain unchanged.
        DISPATCHED/RUNNING imply that volatile execution was interrupted, so they
        are atomically converted into a retry decision without marking the agent
        itself as failed. `incident_id` is optional and is mainly useful for
        isolated integration testing and targeted administrative recovery.
        """

        tasks = await self.repository.list_tasks(
            states=[state.value for state in sorted(IN_FLIGHT_TASK_STATES, key=str)],
            incident_id=incident_id,
            limit=500,
        )
        retrying = 0
        failed = 0
        for task in tasks:
            recovered = await self.mark_execution_failed(
                str(task["task_id"]),
                error_type="backend_restart",
                message="Task execution was interrupted by an agentic backend restart.",
                retryable=True,
            )
            recovered_state = normalize_task_state(recovered["state"])
            if recovered_state == AgentTaskState.RETRYING:
                retrying += 1
            elif recovered_state == AgentTaskState.FAILED:
                failed += 1

        summary = TaskRecoverySummary(
            scanned=len(tasks),
            retrying=retrying,
            failed=failed,
        )
        if summary.scanned:
            LOGGER.warning(
                "Recovered %d interrupted agent tasks: retrying=%d failed=%d",
                summary.scanned,
                summary.retrying,
                summary.failed,
            )
        return summary

    async def _require_task(self, task_id: str) -> dict[str, Any]:
        task = await self.repository.get_task(task_id)
        if task is None:
            raise KeyError(f"Unknown agent task: {task_id}")
        return task

    async def _transition(
        self,
        task: dict[str, Any],
        target: AgentTaskState,
        *,
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current, target_state = validate_task_transition(task["state"], target)
        transitioned = await self.repository.transition_task(
            str(task["task_id"]),
            expected_states=[current.value],
            new_state=target_state.value,
            patch=patch,
        )
        if transitioned is None:
            raise InvalidTaskTransition(
                f"Task {task['task_id']} changed concurrently during "
                f"{current.value} -> {target_state.value}"
            )
        return transitioned
