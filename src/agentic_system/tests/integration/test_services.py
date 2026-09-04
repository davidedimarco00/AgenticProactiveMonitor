import pytest


@pytest.mark.integration
def test_agents_use_the_configured_ollama_models_by_cognitive_role(
    backend_health: dict,
) -> None:
    reasoning_model = str(backend_health["reasoning_model"])
    tool_model = str(backend_health["tool_model"])

    assert reasoning_model.startswith("ollama/")
    assert tool_model.startswith("ollama/")
    assert reasoning_model != tool_model

    for agent in backend_health["agents"]:
        # The SPADE agent provider is Gemma for every role because specialists
        # now reason and finalize diagnoses with Gemma. Qwen is exposed as the
        # specialist-only tool-selection role.
        assert agent["provider_model"] == reasoning_model
        model_roles = agent.get("model_roles") or {}
        assert model_roles["reasoning"] == reasoning_model
        if agent["role"] == "technical_lead":
            assert "tool_selection" not in model_roles
        else:
            assert model_roles["tool_selection"] == tool_model
        assert agent["context_enabled"] is True


@pytest.mark.integration
def test_agents_discover_mcp_tools_for_monitoring_and_rag(backend_health: dict) -> None:
    for agent in backend_health["agents"]:
        tool_names = [str(name) for name in agent["tool_names"]]

        assert agent["mcp_server_count"] == 1
        assert agent["mcp_tool_count"] > 0
        assert any(name.endswith("_ping") for name in tool_names)
        assert any(name.endswith("_search_knowledge") for name in tool_names)
