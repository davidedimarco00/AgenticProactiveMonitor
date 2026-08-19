from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from typing import Any

import pytest


EXPECTED_AGENTS = {
    "technical_lead": "technical-lead@xmpp",
    "system_engineer": "system-engineer@xmpp",
    "network_engineer": "network-engineer@xmpp",
    "application_engineer": "application-engineer@xmpp",
    "software_developer": "software-developer@xmpp",
}


@pytest.mark.integration
def test_backend_reports_five_running_spade_agents(
    backend_health: dict[str, Any],
) -> None:
    """TEST D: the Dockerized backend must expose five active SPADE agents."""

    assert backend_health["status"] == "ok"
    assert backend_health["component"] == "agentic-backend"
    assert backend_health["phase"] == "agents-running"
    assert backend_health["agents_configured"] == 5
    assert backend_health["agents_running"] == 5


@pytest.mark.integration
def test_running_agents_have_expected_distinct_xmpp_identities(
    backend_health: dict[str, Any],
) -> None:
    """Each logical role must be a distinct authenticated SPADE/XMPP identity."""

    agents = backend_health["agents"]
    assert isinstance(agents, list)
    assert len(agents) == 5

    by_role = {agent["role"]: agent for agent in agents}
    assert set(by_role) == set(EXPECTED_AGENTS)

    reported_jids: set[str] = set()
    for role, expected_jid in EXPECTED_AGENTS.items():
        agent = by_role[role]
        assert agent["state"] == "running"
        assert agent["jid"] == expected_jid
        assert agent["started_at"]
        assert agent["last_heartbeat_at"]
        reported_jids.add(agent["jid"])

    assert len(reported_jids) == 5


@pytest.mark.integration
def test_spade_agent_behaviours_keep_updating_heartbeats(
    backend_health: dict[str, Any],
    backend_get_json: Callable[[str], dict[str, Any]],
) -> None:
    """Prove that the five SPADE LifecycleBehaviours remain active after startup."""

    initial = {
        agent["role"]: agent["last_heartbeat_at"]
        for agent in backend_health["agents"]
    }

    deadline = time.monotonic() + 8.0
    latest: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        time.sleep(0.5)
        latest = backend_get_json("/health")
        latest_heartbeats = {
            agent["role"]: agent["last_heartbeat_at"]
            for agent in latest["agents"]
        }

        if all(
            latest_heartbeats[role]
            and latest_heartbeats[role] != initial[role]
            for role in EXPECTED_AGENTS
        ):
            return

    pytest.fail(
        "Not all SPADE agent heartbeats advanced within 8 seconds. "
        f"Initial={initial}; latest={latest}"
    )


@pytest.mark.integration
def test_ready_endpoint_matches_running_backend(
    backend_get_json: Callable[[str], dict[str, Any]],
) -> None:
    payload = backend_get_json("/ready")

    assert payload["status"] == "ok"
    assert payload["phase"] == "agents-running"
    assert payload["agents_running"] == 5


@pytest.mark.integration
def test_xmpp_client_port_is_reachable() -> None:
    """Prosody must be reachable from the host on the configured c2s port."""

    host = os.getenv("AGENTIC_XMPP_TEST_HOST", "127.0.0.1")
    port = int(os.getenv("AGENTIC_XMPP_TEST_PORT", "5222"))

    with socket.create_connection((host, port), timeout=3.0):
        pass
