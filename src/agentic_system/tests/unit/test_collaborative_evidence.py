from __future__ import annotations

import asyncio

from agentic_system.incidents import ReActIncidentCoordinator


class EventRepository:
    async def list_events(self, *, incident_id, limit=200, ascending=True):
        assert incident_id == "INC-COLLAB-001"
        return [
            {
                "event_type": "SPECIALIST_INVESTIGATION_COMPLETED",
                "task_id": "TASK-SYSTEM",
                "agent_role": "system_engineer",
                "outcome": {
                    "summary": "System evidence collected.",
                    "confidence": 0.72,
                    "findings": [
                        "CPU was above the normal baseline during the anomaly window."
                    ],
                    "hypotheses": ["The workload may be application-driven."],
                    "tools_used": ["apm_mcp_get_metrics"],
                },
            },
            {
                "event_type": "PEER_COLLABORATION_AUTHORIZED",
                "task_id": "TASK-APPLICATION",
                "agent_role": "technical_lead",
                "outcome": {
                    "from_role": "system_engineer",
                    "to_role": "application_engineer",
                },
            },
        ]


class TaskWorkflow:
    def __init__(self, repository):
        self.repository = repository


def _coordinator() -> ReActIncidentCoordinator:
    coordinator = object.__new__(ReActIncidentCoordinator)
    coordinator.repository = EventRepository()
    coordinator.task_workflow = TaskWorkflow(coordinator.repository)
    return coordinator


def test_combined_findings_preserve_previous_peer_and_current_specialist_evidence() -> None:
    coordinator = _coordinator()

    findings = asyncio.run(
        coordinator._combined_findings(
            "INC-COLLAB-001",
            current_findings=(
                "Application logs show repeated processing requests in the same interval.",
            ),
        )
    )

    assert findings == [
        "CPU was above the normal baseline during the anomaly window.",
        "Application logs show repeated processing requests in the same interval.",
    ]


def test_collaboration_history_exposes_structured_peer_results_without_hidden_reasoning() -> None:
    coordinator = _coordinator()

    history = asyncio.run(
        coordinator._collaboration_history(
            "INC-COLLAB-001",
            exclude_task_id="TASK-APPLICATION",
        )
    )

    assert history == [
        {
            "task_id": "TASK-SYSTEM",
            "agent_role": "system_engineer",
            "summary": "System evidence collected.",
            "confidence": 0.72,
            "findings": [
                "CPU was above the normal baseline during the anomaly window."
            ],
            "hypotheses": ["The workload may be application-driven."],
            "tools_used": ["apm_mcp_get_metrics"],
        }
    ]
