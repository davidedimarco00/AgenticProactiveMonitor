import pytest

from agentic_system.agents.commands import SpecialistTaskAssignment


def test_specialist_task_assignment_parses_dispatched_payload() -> None:
    assignment = SpecialistTaskAssignment.from_payload(
        {
            "task_id": "TASK-001",
            "incident_id": "INC-001",
            "task_type": "INVESTIGATE_INCIDENT",
            "assigned_to": "network_engineer",
            "attempt": 1,
            "max_attempts": 3,
            "severity": "HIGH",
            "entity": "api-gateway",
            "anomaly": {"detector_id": "NETLAT-api-gateway"},
        }
    )

    assert assignment.task_id == "TASK-001"
    assert assignment.incident_id == "INC-001"
    assert assignment.assigned_to == "network_engineer"
    assert assignment.attempt == 1
    assert assignment.max_attempts == 3
    assert assignment.anomaly["detector_id"] == "NETLAT-api-gateway"


def test_specialist_task_assignment_rejects_non_dispatched_attempt() -> None:
    with pytest.raises(ValueError, match="attempt >= 1"):
        SpecialistTaskAssignment.from_payload(
            {
                "task_id": "TASK-001",
                "incident_id": "INC-001",
                "task_type": "INVESTIGATE_INCIDENT",
                "assigned_to": "system_engineer",
                "attempt": 0,
                "max_attempts": 3,
            }
        )
