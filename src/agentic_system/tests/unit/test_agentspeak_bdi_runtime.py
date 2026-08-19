import asyncio
from pathlib import Path

from agentic_system.reasoning import AgentSpeakBDIRuntime, BDITriageAssessment


def _plan_path() -> Path:
    return (
        Path(__file__).parents[2]
        / "agentic_system"
        / "reasoning"
        / "plans"
        / "technical_lead.asl"
    )


def test_python_agentspeak_executes_triage_and_primary_selection() -> None:
    runtime = AgentSpeakBDIRuntime(
        technical_lead_asl=str(_plan_path()),
        action_timeout_seconds=5.0,
        max_concurrency=1,
    )

    async def fake_triage() -> BDITriageAssessment:
        return BDITriageAssessment(
            probable_domain="system",
            recommended_agent="system_engineer",
            confidence=0.91,
            rationale="System resources are the most appropriate first investigation domain.",
        )

    result = asyncio.run(
        runtime.triage_incident(
            incident_id="INC-TEST-ASL",
            anomaly={
                "detector_id": "detector-1",
                "grade": 1.0,
                "confidence": 0.99,
            },
            available_agents=[
                "system_engineer",
                "network_engineer",
                "application_engineer",
                "software_developer",
            ],
            triage_callback=fake_triage,
        )
    )

    assert result.incident_id == "INC-TEST-ASL"
    assert result.goal == "manage_incident"
    assert result.triage_intention == "triage_incident"
    assert result.selection_intention == "select_primary_investigator"
    assert result.probable_domain == "system"
    assert result.primary_investigator == "system_engineer"
    assert result.confidence == 0.91
