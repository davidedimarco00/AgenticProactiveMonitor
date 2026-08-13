from pytest_bdd import given, then

from tests.integration.conftest import wait_for_backend_ready


@given("the agentic backend is ready for capability checks", target_fixture="health")
def backend_capability_health() -> dict:
    return wait_for_backend_ready()


@then("the reasoning provider is Ollama")
def reasoning_provider_is_ollama(health: dict) -> None:
    assert str(health["provider_model"]).startswith("ollama/")


@then("every agent exposes MCP tools")
def agents_expose_mcp_tools(health: dict) -> None:
    assert all(agent["mcp_server_count"] == 1 for agent in health["agents"])
    assert all(agent["mcp_tool_count"] > 0 for agent in health["agents"])


@then("every agent exposes knowledge search")
def agents_expose_knowledge_search(health: dict) -> None:
    for agent in health["agents"]:
        names = [str(name) for name in agent["tool_names"]]
        assert any(name.endswith("_search_knowledge") for name in names)
