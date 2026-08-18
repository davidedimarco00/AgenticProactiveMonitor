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
    assert config.opensearch_url == "http://opensearch:9200"
    assert config.anomaly_watch_poll_seconds == 5.0
    assert config.anomaly_watch_lookback_seconds == 300
    assert config.incident_correlation_window_seconds == 600
    assert config.jason_bdi_command == "/opt/apm-jason-bdi/bin/apm-jason-bdi"
    assert config.jason_technical_lead_asl.endswith(
        "/agentic_system/bdi/jason/agents/technical_lead.asl"
    )
    assert config.jason_bdi_timeout_seconds == 10.0
    assert config.jason_bdi_max_concurrency == 2
