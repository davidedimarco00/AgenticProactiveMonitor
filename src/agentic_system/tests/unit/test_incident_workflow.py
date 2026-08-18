import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from agentic_system.application.incidents import IncidentWorkflow
from agentic_system.application.ports.incident_assignee import IncidentTriageReceipt
from agentic_system.domain.anomalies import AnomalyObservation
from agentic_system.domain.incidents import IncidentCorrelationPolicy


class FakeIncidentRepository:
    def __init__(self) -> None:
        self.incidents: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.create_calls = 0
        self.update_calls = 0

    async def find_active_incident_by_detector(self, detector_id: str) -> dict[str, Any] | None:
        matches = [
            incident
            for incident in self.incidents.values()
            if incident.get("status") in {
                "NEW",
                "TAKEN_IN_CHARGE",
                "TRIAGED",
                "UNDER_ANALYSIS",
                "DIAGNOSED",
                "OPERATOR_ACTION_REQUIRED",
            }
            and (incident.get("anomaly") or {}).get("detector_id") == detector_id
        ]
        if not matches:
            return None
        return deepcopy(sorted(matches, key=lambda item: item["updated_at"], reverse=True)[0])

    async def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.create_calls += 1
        incident_id = f"INC-TEST-{self.create_calls}"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        incident = deepcopy(payload)
        incident["incident_id"] = incident_id
        incident["created_at"] = now
        incident["updated_at"] = now
        self.incidents[incident_id] = incident
        return deepcopy(incident)

    async def update_incident(
        self,
        incident_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = self.incidents.get(incident_id)
        if current is None:
            return None
        self.update_calls += 1
        current = deepcopy(current)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(deepcopy(value))
            else:
                current[key] = deepcopy(value)
        current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.incidents[incident_id] = current
        return deepcopy(current)

    async def add_event(
        self,
        incident_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if incident_id not in self.incidents:
            return None
        event = deepcopy(payload)
        event["incident_id"] = incident_id
        self.events.append(event)
        return deepcopy(event)


def _observation(*, result_id: str, detector_id: str = "detector-1", grade: float = 1.0) -> AnomalyObservation:
    return AnomalyObservation(
        result_id=result_id,
        result_index=".opendistro-anomaly-results-history-test",
        detector_id=detector_id,
        anomaly_grade=grade,
        confidence=0.95,
        anomaly_score=4.2,
        data_start_time=1_787_057_103_398,
        data_end_time=1_787_057_163_398,
        execution_start_time=1_787_057_223_957,
        execution_end_time=1_787_057_223_957,
    )


def test_first_anomaly_creates_new_incident_and_event() -> None:
    repository = FakeIncidentRepository()
    workflow = IncidentWorkflow(repository, IncidentCorrelationPolicy(window_seconds=600))

    incident = asyncio.run(workflow.handle_anomaly(_observation(result_id="result-a")))

    assert repository.create_calls == 1
    assert repository.update_calls == 0
    assert workflow.created_count == 1
    assert workflow.correlated_count == 0
    assert incident["status"] == "NEW"
    assert incident["anomaly"]["detector_id"] == "detector-1"
    assert incident["anomaly"]["grade"] == 1.0
    assert incident["entity"] == "single-entity-detector:detector-1"
    assert repository.events[0]["event_type"] == "ANOMALY_DETECTED"


def test_second_result_from_same_detector_updates_existing_incident() -> None:
    repository = FakeIncidentRepository()
    workflow = IncidentWorkflow(repository, IncidentCorrelationPolicy(window_seconds=600))

    first = asyncio.run(workflow.handle_anomaly(_observation(result_id="result-a", grade=0.8)))
    second = asyncio.run(workflow.handle_anomaly(_observation(result_id="result-b", grade=1.0)))

    assert first["incident_id"] == second["incident_id"]
    assert repository.create_calls == 1
    assert repository.update_calls == 1
    assert workflow.created_count == 1
    assert workflow.correlated_count == 1
    assert second["anomaly"]["grade"] == 1.0
    assert [event["event_type"] for event in repository.events] == [
        "ANOMALY_DETECTED",
        "ANOMALY_REOBSERVED",
    ]


def test_mark_triaged_persists_coordination_state_without_diagnosis() -> None:
    repository = FakeIncidentRepository()
    workflow = IncidentWorkflow(repository, IncidentCorrelationPolicy(window_seconds=600))
    created = asyncio.run(workflow.handle_anomaly(_observation(result_id="result-a")))
    asyncio.run(
        workflow.mark_taken_in_charge(
            created["incident_id"],
            agent_role="technical_lead",
            agent_jid="technical-lead@xmpp",
        )
    )

    triaged = asyncio.run(
        workflow.mark_triaged(
            IncidentTriageReceipt(
                incident_id=created["incident_id"],
                probable_domain="network",
                primary_investigator="network_engineer",
                confidence=0.84,
                rationale="The detector describes service latency, so network analysis should start first.",
                bdi_goal="manage_incident",
                bdi_intention="select_primary_investigator",
            ),
            agent_role="technical_lead",
            agent_jid="technical-lead@xmpp",
        )
    )

    assert triaged["status"] == "TRIAGED"
    assert triaged["agentic"]["current_agent"] == "technical_lead"
    assert triaged["agentic"]["active_agents"] == ["technical_lead"]
    assert triaged["agentic"]["primary_investigator"] == "network_engineer"
    assert triaged["agentic"]["bdi_goal"] == "manage_incident"
    assert triaged["agentic"]["bdi_intention"] == "select_primary_investigator"
    assert triaged.get("diagnosis", {}) == {}
    assert repository.events[-1]["event_type"] == "INCIDENT_TRIAGED"
    assert repository.events[-1]["outcome"] == "network_engineer"
