from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from agentic_system.agents.messages import AgentMessage
from agentic_system.incidents import (
    InvestigationTaskDispatchReceipt,
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
                "primary_investigator": "system_engineer",
                "investigation_task_id": "TASK-REACT-001",
                "current_agent": "system_engineer",
                "active_agents": ["technical_lead", "system_engineer"],
            },
        }
        self.tasks: dict[str, dict[str, Any]] = {
            "TASK-REACT-001": {
                "task_id": "TASK-REACT-001",
                "incident_id": "INC-REACT-001",
                "task_type": "INVESTIGATE_INCIDENT",
                "assigned_to": "system_engineer",
                "state": "RUNNING",
                "attempt": 1,
                "max_attempts": 3,
            }
        }
        self.events: list[dict[str, Any]] = []

    @property
    def task(self) -> dict[str, Any]:
        return self.tasks["TASK-REACT-001"]

    async def get_incident(self, incident_id: str):
        return deepcopy(self.incident) if incident_id == self.incident["incident_id"] else None

    async def get_task(self, task_id: str):
        task = self.tasks.get(task_id)
        return deepcopy(task) if task is not None else None

    async def list_tasks(self, *, states=None, incident_id=None, limit=200):
        tasks = list(self.tasks.values())
        if incident_id:
            tasks = [task for task in tasks if task["incident_id"] == incident_id]
        if states:
            allowed = {str(state).upper() for state in states}
            tasks = [task for task in tasks if str(task["state"]).upper() in allowed]
        return deepcopy(tasks[:limit])

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

    async def create_investigation_task(
        self,
        incident,
        *,
        primary_investigator,
        created_by="technical_lead",
    ):
        task_id = f"TASK-SUPPORT-{primary_investigator.upper()}"
        task = self.repository.tasks.get(task_id)
        if task is None:
            task = {
                "task_id": task_id,
                "incident_id": incident["incident_id"],
                "task_type": "INVESTIGATE_INCIDENT",
                "created_by": created_by,
                "assigned_to": primary_investigator,
                "state": "PENDING",
                "attempt": 0,
                "max_attempts": 3,
            }
            self.repository.tasks[task_id] = task
        return deepcopy(task)

    async def mark_dispatched(self, task_id: str):
        task = self.repository.tasks[task_id]
        task["state"] = "DISPATCHED"
        task["attempt"] += 1
        return deepcopy(task)

    async def mark_running(self, task_id: str):
        task = self.repository.tasks[task_id]
        task["state"] = "RUNNING"
        return deepcopy(task)

    async def mark_completed(self, task_id: str, *, outcome=None):
        task = self.repository.tasks[task_id]
        task["state"] = "COMPLETED"
        task["outcome"] = deepcopy(outcome)
        return deepcopy(task)

    async def mark_execution_failed(
        self,
        task_id: str,
        *,
        error_type: str,
        message: str,
        retryable: bool = True,
    ):
        task = self.repository.tasks[task_id]
        task["state"] = "RETRYING" if retryable else "FAILED"
        task["last_error"] = {
            "type": error_type,
            "message": message,
            "retryable": retryable,
        }
        return deepcopy(task)


class FakeAgent:
    def __init__(self, role: str) -> None:
        self.role = role
        self.jid = role.replace("_", "-") + "@xmpp"
        self.xmpp_connected = True
        self.communication_ok = True
        self.activity_incident_id: str | None = "INC-REACT-001"
        self.activity_state = "WAITING"
        self.activity_detail: str | None = None
        self.shared_contexts: list[dict[str, Any]] = []

    def set_activity(self, state, *, incident_id=None, detail=None):
        self.activity_state = state
        self.activity_incident_id = incident_id
        self.activity_detail = detail

    async def share_peer_context(self, **kwargs):
        self.shared_contexts.append(deepcopy(kwargs))
        target_role = kwargs["target_role"]
        receiver = kwargs["receiver"]
        return AgentMessage.create(
            type="peer_collaboration_context_accepted",
            sender=receiver,
            receiver=self.jid,
            correlation_id="peer-corr-001",
            payload={
                "accepted_by": target_role,
                "incident_id": kwargs["incident_id"],
                "support_task_id": kwargs["support_task_id"],
            },
        )


class FakeAssignee:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.technical_lead = FakeAgent("technical_lead")
        self.specialists = {
            role: FakeAgent(role)
            for role in (
                "system_engineer",
                "network_engineer",
                "application_engineer",
                "software_developer",
            )
        }
        self.agents: list[Any] = [self.technical_lead, *self.specialists.values()]

    def _technical_lead(self):
        return self.technical_lead

    def _specialist_by_role(self, role):
        return self.specialists[role]

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

    async def dispatch_investigation_task(self, incident, task):
        role = task["assigned_to"]
        return InvestigationTaskDispatchReceipt(
            task_id=task["task_id"],
            incident_id=incident["incident_id"],
            agent_role=role,
            agent_jid=self.specialists[role].jid,
            correlation_id="dispatch-support-001",
            bdi_goal="handle_investigation_task",
            bdi_acceptance_intention="accept_task",
            bdi_investigation_intention="investigate_incident",
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


def test_successful_react_result_authorizes_direct_peer_support() -> None:
    repository = FakeRepository()
    coordinator = _coordinator(repository, decision="request_support")

    asyncio.run(
        coordinator._persist_successful_react_result(
            deepcopy(repository.incident),
            deepcopy(repository.task),
            _success_receipt(),
        )
    )

    support_task = repository.tasks["TASK-SUPPORT-APPLICATION_ENGINEER"]
    assert repository.task["state"] == "COMPLETED"
    assert support_task["state"] == "RUNNING"
    assert repository.incident["status"] == "UNDER_ANALYSIS"
    assert repository.incident["agentic"]["investigation_task_id"] == support_task["task_id"]
    assert repository.incident["agentic"]["current_agent"] == "application_engineer"
    assignee = coordinator.assignee
    primary = assignee.specialists["system_engineer"]
    assert len(primary.shared_contexts) == 1
    assert primary.shared_contexts[0]["target_role"] == "application_engineer"
    assert primary.shared_contexts[0]["support_task_id"] == support_task["task_id"]
    assert any(event["event_type"] == "PEER_COLLABORATION_AUTHORIZED" for event in repository.events)
    assert coordinator.peer_collaborations_started_count == 1


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
