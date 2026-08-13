from types import SimpleNamespace

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
