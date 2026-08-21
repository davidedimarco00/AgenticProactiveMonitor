from __future__ import annotations

import json
from urllib.request import urlopen

import pytest
from websockets.sync.client import connect


EXPECTED_AGENT_HEALTH = {
    "technical_lead": ("technical-lead@xmpp", 8101),
    "system_engineer": ("system-engineer@xmpp", 8102),
    "network_engineer": ("network-engineer@xmpp", 8103),
    "application_engineer": ("application-engineer@xmpp", 8104),
    "software_developer": ("software-developer@xmpp", 8105),
}


def _get_agent_health(port: int) -> dict[str, object]:
    with urlopen(  # noqa: S310 - localhost integration endpoint
        f"http://127.0.0.1:{port}/health",
        timeout=3,
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object from agent health port {port}")
    return payload


def _assert_model_roles(
    role: str,
    payload: dict[str, object],
    backend_health: dict[str, object],
) -> None:
    reasoning_model = str(backend_health["reasoning_model"])
    tool_model = str(backend_health["tool_model"])
    assert payload["provider_model"] == reasoning_model

    model_roles = payload.get("model_roles")
    assert isinstance(model_roles, dict)
    assert model_roles["reasoning"] == reasoning_model
    if role == "technical_lead":
        assert "tool_selection" not in model_roles
    else:
        assert model_roles["tool_selection"] == tool_model


@pytest.mark.integration
def test_each_spade_llm_agent_exposes_its_own_health_port(
    backend_health: dict[str, object],
) -> None:
    """Every logical role must be a live SPADE-LLM agent with MCP and memory."""

    assert backend_health["status"] == "ok"
    assert backend_health["framework"] == "SPADE-LLM"
    assert str(backend_health["reasoning_model"]).startswith("ollama/")
    assert str(backend_health["tool_model"]).startswith("ollama/")
    assert backend_health["reasoning_model"] != backend_health["tool_model"]
    assert backend_health["interaction_memory_enabled"] is True

    for role, (jid, port) in EXPECTED_AGENT_HEALTH.items():
        payload = _get_agent_health(port)
        assert payload["type"] == "agent_health"
        assert payload["role"] == role
        assert payload["jid"] == jid
        assert payload["health_port"] == port
        assert payload["status"] == "ONLINE"
        assert payload["spade_alive"] is True
        assert payload["xmpp_connected"] is True
        assert payload["communication_ok"] is True
        assert payload["framework"] == "SPADE-LLM"
        assert payload["llm_agent"] is True
        _assert_model_roles(role, payload, backend_health)
        assert payload["context_enabled"] is True
        assert payload["interaction_memory_enabled"] is True
        assert payload["mcp_server_count"] == 1
        assert payload["mcp_tool_count"] > 0
        assert isinstance(payload["trace_event_count"], int)
        assert isinstance(payload["trace_tail"], list)

        tool_names = payload["tool_names"]
        assert isinstance(tool_names, list)
        assert len(tool_names) == len(set(tool_names))
        assert "remember_interaction_info" in tool_names
        assert any(str(name).endswith("_ping") for name in tool_names)
        assert payload["mcp_tool_count"] == len(tool_names) - 1

        assert payload["last_heartbeat_at"]
        assert payload["last_communication_at"]


@pytest.mark.integration
def test_global_backend_snapshot_reports_real_agent_communication_health(
    backend_health: dict[str, object],
) -> None:
    """The global readiness snapshot must agree with the five agent endpoints."""

    agents = backend_health["agents"]
    assert isinstance(agents, list)

    by_role = {agent["role"]: agent for agent in agents}
    assert set(by_role) == set(EXPECTED_AGENT_HEALTH)

    for role, (_, port) in EXPECTED_AGENT_HEALTH.items():
        agent = by_role[role]
        assert agent["framework"] == "SPADE-LLM"
        assert agent["llm_agent"] is True
        _assert_model_roles(role, agent, backend_health)
        assert agent["context_enabled"] is True
        assert agent["interaction_memory_enabled"] is True
        assert agent["mcp_tool_count"] > 0
        assert agent["communication_ok"] is True
        assert agent["health_port"] == port
        assert agent["last_communication_at"]
        assert isinstance(agent["trace_event_count"], int)

        tool_names = agent["tool_names"]
        assert isinstance(tool_names, list)
        assert len(tool_names) == len(set(tool_names))
        assert agent["mcp_tool_count"] == len(tool_names) - 1

    assert backend_health["team_communication_ok"] is True
    assert backend_health["unreachable_specialists"] == []


@pytest.mark.integration
def test_each_agent_streams_health_over_websocket(
    backend_health: dict[str, object],
) -> None:
    """The dashboard presence dots depend on five real-time WebSocket streams."""

    assert backend_health["status"] == "ok"

    for role, (jid, port) in EXPECTED_AGENT_HEALTH.items():
        with connect(
            f"ws://127.0.0.1:{port}/ws/health",
            open_timeout=3,
            close_timeout=2,
        ) as websocket:
            raw = websocket.recv(timeout=3)

        payload = json.loads(raw)
        assert payload["type"] == "agent_health"
        assert payload["role"] == role
        assert payload["jid"] == jid
        assert payload["status"] == "ONLINE"
        assert payload["framework"] == "SPADE-LLM"
        assert payload["llm_agent"] is True
        _assert_model_roles(role, payload, backend_health)
        assert payload["xmpp_connected"] is True
        assert payload["communication_ok"] is True
