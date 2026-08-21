import pytest


@pytest.mark.integration
def test_agents_use_the_configured_ollama_provider(backend_health: dict) -> None:
    reasoning_model = str(backend_health["reasoning_model"])
    tool_model = str(backend_health["tool_model"])

    assert reasoning_model.startswith("ollama/")
    assert tool_model.startswith("ollama/")
    assert reasoning_model != tool_model

    for agent in backend_health["agents"]:
        expected_model = (
            reasoning_model if agent["role"] == "technical_lead" else tool_model
        )
        assert agent["provider_model"] == expected_model
        assert agent["context_enabled"] is True


@pytest.mark.integration
def test_agents_discover_mcp_tools_for_monitoring_and_rag(backend_health: dict) -> None:
    for agent in backend_health["agents"]:
        tool_names = [str(name) for name in agent["tool_names"]]

        assert agent["mcp_server_count"] == 1
        assert agent["mcp_tool_count"] > 0
        assert any(name.endswith("_ping") for name in tool_names)
        assert any(name.endswith("_search_knowledge") for name in tool_names)
