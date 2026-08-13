from pathlib import Path

import pytest

from agentic_system.config import EXPECTED_ROLES, load_runtime_config


def test_runtime_config_loads_five_distinct_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = Path(__file__).parents[2] / "config" / "agents.yaml"
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(config_path))

    password_vars = (
        "XMPP_TECHNICAL_LEAD_PASSWORD",
        "XMPP_SYSTEM_ENGINEER_PASSWORD",
        "XMPP_NETWORK_ENGINEER_PASSWORD",
        "XMPP_APPLICATION_ENGINEER_PASSWORD",
        "XMPP_SOFTWARE_DEVELOPER_PASSWORD",
    )
    for name in password_vars:
        monkeypatch.setenv(name, "test-password")

    config = load_runtime_config()

    assert tuple(agent.role for agent in config.agents) == EXPECTED_ROLES
    assert len({agent.jid for agent in config.agents}) == 5
    assert len({agent.health_port for agent in config.agents}) == 5
