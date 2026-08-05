import pytest

from src.agentic_system.models import Evidence, Incident
from src.agentic_system.workspace import IncidentWorkspace


@pytest.mark.asyncio
async def test_workspace_stores_incident_and_evidence() -> None:
    workspace = IncidentWorkspace()
    incident = Incident(
        detector_id="detector-1",
        host_id="machine-03",
        metric_name="cpu.usage_active",
        anomaly_score=0.9,
    )
    await workspace.create(incident)
    await workspace.add_evidence(Evidence(
        incident_id=incident.incident_id,
        source_agent="metrics@localhost",
        evidence_type="metric",
        summary="CPU high",
        confidence=0.9,
    ))
    stored = await workspace.get(incident.incident_id)
    assert len(stored.evidence) == 1
    assert stored.evidence[0].summary == "CPU high"
