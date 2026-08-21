from pathlib import Path

import pytest

from agentic_system.settings import EXPECTED_ROLES, load_runtime_config


def _configure_agent_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    password_vars = (
        "XMPP_TECHNICAL_LEAD_PASSWORD",
        "XMPP_SYSTEM_ENGINEER_PASSWORD",
        "XMPP_NETWORK_ENGINEER_PASSWORD",
        "XMPP_APPLICATION_ENGINEER_PASSWORD",
        "XMPP_SOFTWARE_DEVELOPER_PASSWORD",
    )
    for name in password_vars:
        monkeypatch.setenv(name, "test-password")


def _config_path() -> Path:
    return Path(__file__).parents[2] / "config" / "agents.yaml"


def test_runtime_config_loads_five_distinct_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(_config_path()))
    monkeypatch.delenv("AGENT_MAX_LLM_CONCURRENCY", raising=False)
    monkeypatch.delenv("AGENT_REACT_MAX_STEPS", raising=False)
    monkeypatch.delenv("AGENT_REACT_TOOL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ENABLE_TEST_ANOMALY_INJECTION", raising=False)
    monkeypatch.delenv("ENABLE_OPENSEARCH_ANOMALY_WATCHER", raising=False)
    _configure_agent_passwords(monkeypatch)

    config = load_runtime_config()

    assert tuple(agent.role for agent in config.agents) == EXPECTED_ROLES
    assert len({agent.jid for agent in config.agents}) == 5
    assert len({agent.health_port for agent in config.agents}) == 5
    assert config.opensearch_url == "http://opensearch:9200"
    assert config.anomaly_watch_poll_seconds == 5.0
    assert config.anomaly_watch_lookback_seconds == 300
    assert config.incident_correlation_window_seconds == 600
    assert config.agentspeak_technical_lead_asl.endswith(
        "/agentic_system/reasoning/plans/technical_lead.asl"
    )
    assert config.agentspeak_specialist_asl.endswith(
        "/agentic_system/reasoning/plans/specialist.asl"
    )
    assert config.agentspeak_action_timeout_seconds == 120.0
    assert config.agentspeak_bdi_max_concurrency == 2
    assert config.task_dispatch_timeout_seconds == 10.0
    assert config.max_llm_concurrency == 1
    assert config.react_max_steps == 10
    assert config.react_tool_timeout_seconds == 30.0
    assert config.enable_test_anomaly_injection is False
    assert config.enable_opensearch_anomaly_watcher is True


def test_runtime_config_enables_explicit_test_anomaly_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(_config_path()))
    monkeypatch.setenv("ENABLE_TEST_ANOMALY_INJECTION", "1")
    monkeypatch.setenv("ENABLE_OPENSEARCH_ANOMALY_WATCHER", "0")
    _configure_agent_passwords(monkeypatch)

    config = load_runtime_config()

    assert config.enable_test_anomaly_injection is True
    assert config.enable_opensearch_anomaly_watcher is False


def test_runtime_config_rejects_non_positive_llm_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(_config_path()))
    monkeypatch.setenv("AGENT_MAX_LLM_CONCURRENCY", "0")
    _configure_agent_passwords(monkeypatch)

    with pytest.raises(RuntimeError, match="AGENT_MAX_LLM_CONCURRENCY"):
        load_runtime_config()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENT_REACT_MAX_STEPS", "0"),
        ("AGENT_REACT_TOOL_TIMEOUT_SECONDS", "0"),
    ],
)
def test_runtime_config_rejects_non_positive_react_limits(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(_config_path()))
    monkeypatch.setenv(name, value)
    _configure_agent_passwords(monkeypatch)

    with pytest.raises(RuntimeError, match=name):
        load_runtime_config()
