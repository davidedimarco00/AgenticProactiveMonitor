from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from spade_llm.context import ContextManager
from spade_llm.mcp import StreamableHttpServerConfig, get_all_mcp_tools

from agentic_system.agents.factory import MCP_SERVER_NAME
from agentic_system.reasoning import RoleLLMProvider, SharedInferenceGate, SpecialistReActExecutor


RUN_LIVE_MODEL_ROUTING = os.getenv("RUN_LIVE_MODEL_ROUTING", "0") == "1"


async def _exercise_live_model_routing() -> None:
    config = SimpleNamespace(
        tool_model=os.getenv("LIVE_TOOL_MODEL", "qwen3.5:4b"),
        embedding_model=os.getenv("LIVE_EMBEDDING_MODEL", "ibm/granite-embedding:30m"),
        ollama_url=os.getenv("LIVE_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    provider = RoleLLMProvider(
        model=config.tool_model,
        base_url=config.ollama_url,
        embedding_model=config.embedding_model,
        gate=SharedInferenceGate(1),
    )

    mcp_server = StreamableHttpServerConfig(
        name=MCP_SERVER_NAME,
        url=os.getenv("LIVE_MCP_URL", "http://127.0.0.1:8000/mcp"),
        cache_tools=True,
    )
    tools = await get_all_mcp_tools([mcp_server])
    assert tools, "The live MCP server did not expose any SPADE-LLM tools"

    ping_tools = [tool for tool in tools if tool.name.endswith("_ping")]
    assert ping_tools, "The live MCP server did not expose its ping tool"

    routing_tools = [ping_tools[0]]
    knowledge_tools = [tool for tool in tools if tool.name.endswith("_search_knowledge")]
    if knowledge_tools:
        routing_tools.append(knowledge_tools[0])

    executor = SpecialistReActExecutor(
        provider=provider,
        context=ContextManager(
            system_prompt=(
                "You are a system monitoring specialist. Use supplied tools for live checks."
            )
        ),
        tools=routing_tools,
        max_steps=10,
        tool_timeout_seconds=30.0,
    )
    result = await executor.investigate(
        task_id="LIVE-LANGCHAIN-ROUTING",
        incident_id="LIVE-MCP-AVAILABILITY",
        agent_role="system_engineer",
        severity="MEDIUM",
        entity="agentic-mcp-server",
        anomaly={
            "instruction": (
                "Verify whether the AgenticProactiveMonitor MCP server is available right now. "
                "Use the ping tool as live evidence and do not answer from prior knowledge."
            )
        },
    )

    assert any(name.endswith("_ping") for name in result.tools_used), (
        f"Expected Qwen/LangChain to execute the MCP ping tool, got {result.tools_used!r}"
    )
    ping_evidence = [item for item in result.evidence if item["tool"].endswith("_ping")]
    assert ping_evidence
    assert ping_evidence[0]["success"] is True
    assert "ok" in str(ping_evidence[0]["observation"]).lower()


@pytest.mark.integration
@pytest.mark.skipif(
    not RUN_LIVE_MODEL_ROUTING,
    reason="Set RUN_LIVE_MODEL_ROUTING=1 to run real Qwen/LangChain/Ollama tool routing",
)
def test_live_qwen_langchain_selects_and_executes_real_mcp_tool() -> None:
    asyncio.run(_exercise_live_model_routing())
