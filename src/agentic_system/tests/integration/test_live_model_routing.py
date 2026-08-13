from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from spade_llm.context import ContextManager
from spade_llm.mcp import StreamableHttpServerConfig, get_all_mcp_tools

from agentic_system.providers import HybridLLMProvider


RUN_LIVE_MODEL_ROUTING = os.getenv("RUN_LIVE_MODEL_ROUTING", "0") == "1"


async def _exercise_live_model_routing() -> None:
    config = SimpleNamespace(
        reasoning_model=os.getenv("LIVE_REASONING_MODEL", "gemma4:e2b"),
        tool_model=os.getenv("LIVE_TOOL_MODEL", "qwen3.5:4b"),
        embedding_model=os.getenv("LIVE_EMBEDDING_MODEL", "ibm/granite-embedding:30m"),
        ollama_url=os.getenv("LIVE_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    provider = HybridLLMProvider.from_runtime(config)

    mcp_server = StreamableHttpServerConfig(
        name="apm",
        url=os.getenv("LIVE_MCP_URL", "http://127.0.0.1:8000/mcp"),
        cache_tools=True,
    )
    tools = await get_all_mcp_tools([mcp_server])
    assert tools, "The live MCP server did not expose any SPADE-LLM tools"

    tool_by_name = {tool.name: tool for tool in tools}
    ping_tools = [name for name in tool_by_name if name.endswith("_ping")]
    assert ping_tools, "The live MCP server did not expose its ping tool"

    conversation_id = "live-model-routing"
    context = ContextManager(system_prompt="You are a monitoring specialist. Use an available tool when the user explicitly requests a live verification.")
    context.add_message_dict(
        {"role": "user", "content": "Verify that the AgenticProactiveMonitor MCP server is currently available. You MUST use the available ping tool before answering."},
        conversation_id,
    )

    response = await provider.get_llm_response(context, tools=tools, conversation_id=conversation_id)
    tool_calls = response.get("tool_calls") or []
    assert tool_calls, "Qwen did not produce a tool call after Gemma reasoning"

    selected = tool_calls[0]
    selected_name = str(selected.get("name", ""))
    assert selected_name in tool_by_name
    assert selected_name.endswith("_ping"), f"Expected Qwen to select the MCP ping tool, got {selected_name}"

    arguments = selected.get("arguments") or {}
    assert isinstance(arguments, dict)

    result = await tool_by_name[selected_name].execute(**arguments)
    assert "ok" in str(result).lower(), f"The MCP ping tool did not return OK: {result!r}"


@pytest.mark.integration
@pytest.mark.skipif(not RUN_LIVE_MODEL_ROUTING, reason="Set RUN_LIVE_MODEL_ROUTING=1 to run real Gemma/Qwen/Ollama tool routing")
def test_live_gemma_reasoning_qwen_selects_and_executes_real_mcp_tool() -> None:
    asyncio.run(_exercise_live_model_routing())
