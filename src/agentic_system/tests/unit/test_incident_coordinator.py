import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from agentic_system.application.incidents import IncidentCoordinator, IncidentWorkflow
from agentic_system.application.ports.incident_assignee import IncidentAssigneeReceipt
from agentic_system.domain.anomalies import AnomalyObservation
from agentic_system.domain.incidents import IncidentCorrelationPolicy


class FakeRepository:
    def __init__(self) -> None:
        self.incidents: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []

    async def find_active_incident_by_detector(self, detector_id: str) -> dict[str, Any] | None:
        for incident in self.incidents.values():
            if (incident.get("anomaly") or {}).get("detector_id") == detector_id:
                return deepcopy(incident)
        return None

    async def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        incident = deepcopy(payload)
        incident.update(
            {
                "incident_id": "INC-TEST-1",
                "created_at": now,
                "updated_at": now,
            }
        )
        self.incidents["INC-TEST-1"] = incident
        return deepcopy(incident)

    async def update_incident(self, incident_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        current = self.incidents.get(incident_id)
        if current is None:
            return None
        for key, value in deepcopy(patch).items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(value)
            else:
                current[key] = value
        current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return deepcopy(current)

    async def add_event(self, incident_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if incident_id not in self.incidents:
            return None
        event = deepcopy(payload)
        event["incident_id"] = incident_id
        self.events.append(event)
        return event


class FakeAssignee:
    def __init__(self) -> None:
        self.calls = 0

    async def assign_incident(self, incident: dict[str, Any]) -> IncidentAssigneeReceipt:
        self.calls += 1
        return IncidentAssigneeReceipt(
            incident_id=str(incident["incident_id"]),
            agent_role="technical_lead",
            agent_jid="technical-lead@xmpp",
        )


def _observation(result_id: str) -> AnomalyObservation:
    return AnomalyObservation(
        result_id=result_id,
        result_index=".opendistro-anomaly-results-history-test",
        detector_id="detector-1",
        anomaly_grade=1.0,
        confidence=0.99,
        anomaly_score=4.0,
        data_start_time=None,
        data_end_time=None,
        execution_start_time=None,
        execution_end_time=None,
    )


def test_new_incident_is_assigned_once_and_marked_taken_in_charge() -> None:
    repository = FakeRepository()
    assignee = FakeAssignee()
    workflow = IncidentWorkflow(repository, IncidentCorrelationPolicy(window_seconds=600))
    coordinator = IncidentCoordinator(workflow, assignee)

    first = asyncio.run(coordinator.handle_anomaly(_observation("result-a")))
    second = asyncio.run(coordinator.handle_anomaly(_observation("result-b")))

    assert first.created is True
    assert first["status"] == "TAKEN_IN_CHARGE"
    assert first["agentic"]["current_agent"] == "technical_lead"
    assert second.created is False
    assert second.correlated is True
    assert assignee.calls == 1
    assert coordinator.assigned_count == 1
    assert [event["event_type"] for event in repository.events] == [
        "ANOMALY_DETECTED",
        "INCIDENT_TAKEN_IN_CHARGE",
        "ANOMALY_REOBSERVED",
    ]
