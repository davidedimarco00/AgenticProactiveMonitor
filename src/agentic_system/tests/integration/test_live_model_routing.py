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
    """Verify the real Gemma -> Qwen -> MCP -> Gemma routing path.

    This test deliberately stops before diagnostic finalization. Its purpose is to
    validate cognitive model routing and real MCP execution, not to force a generic
    connectivity probe into the causal incident-diagnosis schema.
    """

    config = SimpleNamespace(
        reasoning_model=os.getenv("LIVE_REASONING_MODEL", "gemma4:e2b"),
        tool_model=os.getenv("LIVE_TOOL_MODEL", "qwen3.5:4b"),
        embedding_model=os.getenv("LIVE_EMBEDDING_MODEL", "ibm/granite-embedding:30m"),
        ollama_url=os.getenv("LIVE_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    gate = SharedInferenceGate(1)
    reasoning_provider = RoleLLMProvider(
        model=config.reasoning_model,
        base_url=config.ollama_url,
        embedding_model=config.embedding_model,
        gate=gate,
    )
    tool_provider = RoleLLMProvider(
        model=config.tool_model,
        base_url=config.ollama_url,
        embedding_model=config.embedding_model,
        gate=gate,
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

    # Keep a competing RAG tool, when available, so Qwen must actually select
    # the operational action requested by Gemma instead of having one option.
    routing_tools = [ping_tools[0]]
    knowledge_tools = [tool for tool in tools if tool.name.endswith("_search_knowledge")]
    if knowledge_tools:
        routing_tools.append(knowledge_tools[0])

    executor = SpecialistReActExecutor(
        provider=reasoning_provider,
        tool_provider=tool_provider,
        context=ContextManager(
            system_prompt=(
                "You are a system monitoring specialist. Use supplied tools for live checks."
            )
        ),
        tools=routing_tools,
        max_steps=10,
        tool_timeout_seconds=30.0,
    )

    assignment = {
        "task_id": "LIVE-HYBRID-ROUTING",
        "incident_id": "LIVE-MCP-AVAILABILITY",
        "agent_role": "system_engineer",
        "severity": "MEDIUM",
        "entity": "agentic-mcp-server",
        "anomaly": {
            "instruction": (
                "Verify whether the AgenticProactiveMonitor MCP server is available right now. "
                "Use the ping tool as live evidence and do not answer from prior knowledge."
            )
        },
    }

    # Gemma reasons about what evidence is needed. With no evidence collected,
    # the policy forbids diagnostic closure.
    first_decision = await executor._reason(  # noqa: SLF001 - integration seam
        assignment=assignment,
        evidence=[],
        decisions=[],
    )
    assert first_decision.action == "gather_evidence"
    assert first_decision.evidence_needed

    # Qwen maps Gemma's evidence request to exactly one real MCP tool call.
    tool_name, arguments = await executor._select_tool(  # noqa: SLF001 - integration seam
        assignment=assignment,
        evidence_needed=first_decision.evidence_needed,
        evidence=[],
    )
    assert tool_name.endswith("_ping"), (
        f"Expected Qwen to select the MCP ping tool after Gemma reasoning, got {tool_name!r}"
    )

    observation = await executor._execute_tool(  # noqa: SLF001 - integration seam
        step=1,
        tool_name=tool_name,
        arguments=arguments,
    )
    assert observation.success is True
    assert "ok" in str(observation.observation).lower()

    # Feed the real MCP observation back to Gemma to verify the Observe -> Reason
    # half of the hybrid loop as well. We intentionally do not require a causal
    # diagnosis here because an availability probe is not an incident root cause.
    second_decision = await executor._reason(  # noqa: SLF001 - integration seam
        assignment=assignment,
        evidence=[observation],
        decisions=[first_decision],
    )
    assert second_decision.action in {"finish", "gather_evidence"}
    assert second_decision.decision_summary


@pytest.mark.integration
@pytest.mark.skipif(
    not RUN_LIVE_MODEL_ROUTING,
    reason=(
        "Set RUN_LIVE_MODEL_ROUTING=1 to run real Gemma reasoning + "
        "Qwen tool-selection + MCP routing"
    ),
)
def test_live_gemma_reasoning_qwen_selects_and_executes_real_mcp_tool() -> None:
    asyncio.run(_exercise_live_model_routing())
