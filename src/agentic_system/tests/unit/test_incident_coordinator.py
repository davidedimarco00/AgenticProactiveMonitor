import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from agentic_system.application.incidents import IncidentCoordinator, IncidentWorkflow
from agentic_system.application.ports.incident_assignee import (
    IncidentAssigneeReceipt,
    IncidentTriageReceipt,
)
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
                "incident_id": "INC-20260818-001",
                "created_at": now,
                "updated_at": now,
            }
        )
        self.incidents["INC-20260818-001"] = incident
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


class FakeDetectorContext:
    def __init__(self) -> None:
        self.calls = 0

    async def get_detector_context(self, detector_id: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "detector_id": detector_id,
            "detector_type": "SINGLE_ENTITY",
            "name": "CPU-processing-service",
            "description": "Container CPU usage anomaly detector for processing-service",
            "indices": ["metrics-processing-service-*"],
        }


class FakeAssignee:
    def __init__(self) -> None:
        self.assign_calls = 0
        self.triage_calls = 0

    async def assign_incident(self, incident: dict[str, Any]) -> IncidentAssigneeReceipt:
        self.assign_calls += 1
        assert incident["entity"] == "CPU-processing-service"
        assert incident["anomaly"]["detector_name"] == "CPU-processing-service"
        return IncidentAssigneeReceipt(
            incident_id=str(incident["incident_id"]),
            agent_role="technical_lead",
            agent_jid="technical-lead@xmpp",
        )

    async def triage_incident(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
    ) -> IncidentTriageReceipt:
        self.triage_calls += 1
        assert incident["status"] == "TAKEN_IN_CHARGE"
        assert detector_context["detector_type"] == "SINGLE_ENTITY"
        return IncidentTriageReceipt(
            incident_id=str(incident["incident_id"]),
            probable_domain="system",
            primary_investigator="system_engineer",
            confidence=0.91,
            rationale="CPU detector metadata makes system resources the best first domain to inspect.",
            bdi_goal="manage_incident",
            bdi_triage_intention="triage_incident",
            bdi_intention="select_primary_investigator",
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


def test_new_incident_is_triaged_once_without_technical_lead_diagnosis() -> None:
    repository = FakeRepository()
    assignee = FakeAssignee()
    detector_context = FakeDetectorContext()
    workflow = IncidentWorkflow(repository, IncidentCorrelationPolicy(window_seconds=600))
    coordinator = IncidentCoordinator(workflow, assignee, detector_context)

    first = asyncio.run(coordinator.handle_anomaly(_observation("result-a")))
    second = asyncio.run(coordinator.handle_anomaly(_observation("result-b")))

    assert first.created is True
    assert first["status"] == "TRIAGED"
    assert first["incident_id"] == "INC-20260818-001"
    assert first["entity"] == "CPU-processing-service"
    assert first["anomaly"]["detector_name"] == "CPU-processing-service"
    assert first["agentic"] == {
        "current_agent": "technical_lead",
        "active_agents": ["technical_lead"],
        "primary_investigator": "system_engineer",
        "triage_domain": "system",
        "triage_confidence": 0.91,
        "triage_rationale": (
            "CPU detector metadata makes system resources the best first domain to inspect."
        ),
        "bdi_goal": "manage_incident",
        "bdi_triage_intention": "triage_incident",
        "bdi_intention": "select_primary_investigator",
    }
    assert first.get("diagnosis", {}) == {}

    assert second.created is False
    assert second.correlated is True
    assert second["entity"] == "CPU-processing-service"
    assert assignee.assign_calls == 1
    assert assignee.triage_calls == 1
    assert detector_context.calls == 2
    assert coordinator.assigned_count == 1
    assert coordinator.triaged_count == 1
    assert [event["event_type"] for event in repository.events] == [
        "ANOMALY_DETECTED",
        "INCIDENT_TAKEN_IN_CHARGE",
        "INCIDENT_TRIAGED",
        "ANOMALY_REOBSERVED",
    ]
