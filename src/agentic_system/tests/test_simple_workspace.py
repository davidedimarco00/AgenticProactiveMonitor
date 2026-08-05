import pytest

from src.agentic_system.simple.models import IncidentContext
from src.agentic_system.simple.workspace import Workspace


@pytest.mark.asyncio
async def test_simple_workspace_round_and_evidence():
    workspace = Workspace()
    incident = IncidentContext(detector_id="d1", host_id="machine-03", metric_name="cpu.usage_active", anomaly_score=0.9)
    await workspace.create(incident)
    await workspace.begin_round(incident.incident_id)
    await workspace.add_evidence(incident.incident_id, {"source": "test"})
    stored = await workspace.get(incident.incident_id)
    assert stored.round == 1
    assert stored.evidence == [{"source": "test"}]
