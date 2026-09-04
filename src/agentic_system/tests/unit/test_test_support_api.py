from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_system.api.test_support import attach_test_support_api


class FakeIntake:
    def __init__(self) -> None:
        self.observations = []

    async def enqueue(self, observation) -> bool:
        self.observations.append(observation)
        return True


class FakeAgent:
    def __init__(self, role: str, incident_id: str | None = None) -> None:
        self.role = role
        self.activity_incident_id = incident_id
        self.activity_state = "WORKING" if incident_id else "IDLE"
        self.activity_detail = None

    def set_activity(self, state: str, *, incident_id=None, detail=None) -> None:
        self.activity_state = state
        self.activity_incident_id = incident_id
        self.activity_detail = detail


class FakeRuntime:
    def __init__(self) -> None:
        self.anomaly_intake = FakeIntake()
        self.agents = [FakeAgent("technical_lead"), FakeAgent("system_engineer")]

    def anomaly_watch_snapshot(self) -> dict[str, Any]:
        return {"queue_depth": len(self.anomaly_intake.observations)}


class FakeRepository:
    def __init__(self) -> None:
        self.incident = {
            "incident_id": "INC-TEST-001",
            "status": "TRIAGED",
            "agentic": {"investigation_task_id": "TASK-TEST-001"},
        }
        self.task = {
            "task_id": "TASK-TEST-001",
            "incident_id": "INC-TEST-001",
            "state": "RUNNING",
        }
        self.events = []

    async def get_incident(self, incident_id: str):
        return dict(self.incident) if incident_id == self.incident["incident_id"] else None

    async def get_task(self, task_id: str):
        return dict(self.task) if task_id == self.task["task_id"] else None

    async def update_incident(self, incident_id: str, patch: dict[str, Any]):
        if incident_id != self.incident["incident_id"]:
            return None
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(self.incident.get(key), dict):
                self.incident[key] = {**self.incident[key], **value}
            else:
                self.incident[key] = value
        return dict(self.incident)

    async def add_event(self, incident_id: str, event: dict[str, Any]):
        self.events.append({"incident_id": incident_id, **event})
        return self.events[-1]


class FakeTaskWorkflow:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.completed_calls = 0

    async def mark_completed(self, task_id: str, *, outcome=None):
        assert task_id == self.repository.task["task_id"]
        self.completed_calls += 1
        self.repository.task["state"] = "COMPLETED"
        self.repository.task["outcome"] = dict(outcome or {})
        return dict(self.repository.task)


def _client():
    app = FastAPI()
    runtime = FakeRuntime()
    repository = FakeRepository()
    task_workflow = FakeTaskWorkflow(repository)
    runtime.agents = [
        FakeAgent("technical_lead", "INC-TEST-001"),
        FakeAgent("system_engineer", "INC-TEST-001"),
    ]
    attach_test_support_api(
        app,
        runtime=runtime,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        task_workflow=task_workflow,  # type: ignore[arg-type]
    )
    return TestClient(app), runtime, repository, task_workflow


def test_test_anomaly_injection_enters_normal_runtime_intake() -> None:
    client, runtime, _repository, _workflow = _client()

    response = client.post(
        "/internal/v1/test/anomalies",
        json={
            "detector_id": "mock-cpu-processing",
            "detector_name": "CPU-processing-service",
            "anomaly_grade": 1.0,
            "confidence": 0.96,
            "anomaly_score": 8.1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["source"] == "test_injector"
    assert len(runtime.anomaly_intake.observations) == 1
    observation = runtime.anomaly_intake.observations[0]
    assert observation.detector_name == "CPU-processing-service"
    assert observation.source == "test_injector"
    assert observation.result_index == "agentic-test-anomaly-results"


def test_test_completion_can_close_legacy_running_task() -> None:
    client, runtime, repository, workflow = _client()

    response = client.post(
        "/internal/v1/test/incidents/INC-TEST-001/complete",
        json={"summary": "Synthetic TL acceptance for FIFO release test."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert repository.task["state"] == "COMPLETED"
    assert workflow.completed_calls == 1
    assert repository.incident["status"] == "RESOLVED"
    assert repository.events[-1]["event_type"] == "TEST_TECHNICAL_LEAD_ACCEPTED"
    assert all(agent.activity_state == "IDLE" for agent in runtime.agents)


def test_test_completion_accepts_already_completed_react_task_without_retransition() -> None:
    client, _runtime, repository, workflow = _client()
    repository.incident["status"] = "UNDER_ANALYSIS"
    repository.task["state"] = "COMPLETED"
    repository.task["outcome"] = {
        "status": "completed",
        "summary": "Real ReAct result already persisted.",
    }

    response = client.post(
        "/internal/v1/test/incidents/INC-TEST-001/complete",
        json={"summary": "TL accepts the real ReAct result for this test."},
    )

    assert response.status_code == 200
    assert repository.incident["status"] == "RESOLVED"
    assert repository.task["state"] == "COMPLETED"
    assert workflow.completed_calls == 0
    assert repository.events[-1]["event_type"] == "TEST_TECHNICAL_LEAD_ACCEPTED"


def test_test_completion_refuses_non_running_non_completed_task() -> None:
    client, _runtime, repository, _workflow = _client()
    repository.task["state"] = "PENDING"

    response = client.post(
        "/internal/v1/test/incidents/INC-TEST-001/complete",
        json={},
    )

    assert response.status_code == 409
    assert "COMPLETED" in response.json()["detail"]
