from types import SimpleNamespace

import pytest

from agentic_system.agents.base import BaseAgent


def test_communication_becomes_healthy_only_with_xmpp_connection() -> None:
    connected = SimpleNamespace(
        xmpp_connected=True,
        communication_ok=False,
        last_communication_at=None,
    )
    disconnected = SimpleNamespace(
        xmpp_connected=False,
        communication_ok=False,
        last_communication_at=None,
    )

    BaseAgent.mark_communication_ok(connected)
    BaseAgent.mark_communication_ok(disconnected)

    assert connected.communication_ok is True
    assert connected.last_communication_at is not None
    assert disconnected.communication_ok is False
    assert disconnected.last_communication_at is None


def test_mark_communication_failed_clears_health_flag() -> None:
    agent = SimpleNamespace(communication_ok=True)

    BaseAgent.mark_communication_failed(agent)

    assert agent.communication_ok is False


def test_agent_activity_is_explicit_and_tracks_incident_context() -> None:
    agent = SimpleNamespace(
        activity_state="IDLE",
        activity_incident_id=None,
        activity_detail=None,
        activity_updated_at=None,
    )

    BaseAgent.set_activity(
        agent,
        "WORKING",
        incident_id="INC-20260818-001",
        detail="triaging_incident",
    )

    assert agent.activity_state == "WORKING"
    assert agent.activity_incident_id == "INC-20260818-001"
    assert agent.activity_detail == "triaging_incident"
    assert agent.activity_updated_at is not None

    BaseAgent.set_activity(
        agent,
        "WAITING",
        incident_id="INC-20260818-001",
        detail="primary_investigator_selected",
    )

    assert agent.activity_state == "WAITING"
    assert agent.activity_incident_id == "INC-20260818-001"


def test_agent_activity_rejects_unknown_state() -> None:
    agent = SimpleNamespace(
        activity_state="IDLE",
        activity_incident_id=None,
        activity_detail=None,
        activity_updated_at=None,
    )

    with pytest.raises(ValueError, match="Unsupported agent activity state"):
        BaseAgent.set_activity(agent, "BUSY")
