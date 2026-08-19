import pytest


@pytest.mark.integration
def test_agents_use_the_configured_ollama_provider(backend_health: dict) -> None:
    provider_model = str(backend_health["provider_model"])
    assert provider_model.startswith("ollama/")

    for agent in backend_health["agents"]:
        assert agent["provider_model"] == provider_model
        assert agent["context_enabled"] is True


@pytest.mark.integration
def test_agents_discover_mcp_tools_for_monitoring_and_rag(backend_health: dict) -> None:
    for agent in backend_health["agents"]:
        tool_names = [str(name) for name in agent["tool_names"]]

        assert agent["mcp_server_count"] == 1
        assert agent["mcp_tool_count"] > 0
        assert any(name.endswith("_ping") for name in tool_names)
        assert any(name.endswith("_search_knowledge") for name in tool_names)
