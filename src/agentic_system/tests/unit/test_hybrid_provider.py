import asyncio
from types import SimpleNamespace
from typing import Any

from spade_llm.context import ContextManager
from spade_llm.tools import LLMTool

from agentic_system.agents.factory import MCP_SERVER_NAME
from agentic_system.providers import HybridLLMProvider
from agentic_system.providers.hybrid import OllamaToolCallingProvider


class FakeProvider:
    def __init__(self, model: str, response: dict[str, Any]) -> None:
        self.model = model
        self.base_url = "http://ollama:11434"
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.embedding_calls: list[list[str]] = []

    async def get_llm_response(
        self,
        context: Any,
        tools: Any = None,
        conversation_id: str | None = None,
        output_schema: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": context.get_prompt(conversation_id),
                "tools": tools,
                "conversation_id": conversation_id,
                "output_schema": output_schema,
            }
        )
        return dict(self.response)

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.embedding_calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


def _context() -> ContextManager:
    context = ContextManager(system_prompt="You are a monitoring specialist.")
    context.add_message_dict(
        {"role": "user", "content": "Investigate the processing-service CPU anomaly."},
        "incident-1",
    )
    return context


def test_hybrid_provider_runs_reasoning_before_tool_selection() -> None:
    reasoning = FakeProvider(
        "ollama/gemma4:e2b",
        {"text": "I need current CPU metrics.", "tool_calls": [], "structured": None},
    )
    selector = FakeProvider(
        "ollama_native/qwen3.5:4b",
        {
            "text": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "get_metrics",
                    "arguments": {"service": "processing-service"},
                }
            ],
            "structured": None,
        },
    )
    embeddings = FakeProvider(
        "ollama/ibm/granite-embedding:30m",
        {"text": None, "tool_calls": [], "structured": None},
    )
    provider = HybridLLMProvider(
        reasoning_provider=reasoning,  # type: ignore[arg-type]
        tool_provider=selector,  # type: ignore[arg-type]
        embedding_provider=embeddings,  # type: ignore[arg-type]
    )
    tools = [object()]

    result = asyncio.run(
        provider.get_llm_response(
            _context(),
            tools=tools,  # type: ignore[arg-type]
            conversation_id="incident-1",
        )
    )

    assert reasoning.calls[0]["tools"] is None
    assert selector.calls[0]["tools"] is tools
    assert result["tool_calls"][0]["name"] == "get_metrics"
    assert any(
        "Reasoning draft from the reasoning model" in str(message.get("content", ""))
        for message in selector.calls[0]["prompt"]
    )


def test_hybrid_provider_returns_gemma_answer_when_qwen_selects_no_tool() -> None:
    reasoning = FakeProvider(
        "ollama/gemma4:e2b",
        {"text": "The available evidence is sufficient.", "tool_calls": [], "structured": None},
    )
    selector = FakeProvider(
        "ollama_native/qwen3.5:4b",
        {"text": "No tool required.", "tool_calls": [], "structured": None},
    )
    embeddings = FakeProvider(
        "ollama/ibm/granite-embedding:30m",
        {"text": None, "tool_calls": [], "structured": None},
    )
    provider = HybridLLMProvider(
        reasoning_provider=reasoning,  # type: ignore[arg-type]
        tool_provider=selector,  # type: ignore[arg-type]
        embedding_provider=embeddings,  # type: ignore[arg-type]
    )

    result = asyncio.run(
        provider.get_llm_response(
            _context(),
            tools=[object()],  # type: ignore[arg-type]
            conversation_id="incident-1",
        )
    )

    assert result["text"] == "The available evidence is sufficient."
    assert result["tool_calls"] == []


def test_hybrid_provider_delegates_embeddings_to_granite() -> None:
    reasoning = FakeProvider(
        "ollama/gemma4:e2b",
        {"text": "", "tool_calls": [], "structured": None},
    )
    selector = FakeProvider(
        "ollama_native/qwen3.5:4b",
        {"text": "", "tool_calls": [], "structured": None},
    )
    embeddings = FakeProvider(
        "ollama/ibm/granite-embedding:30m",
        {"text": "", "tool_calls": [], "structured": None},
    )
    provider = HybridLLMProvider(
        reasoning_provider=reasoning,  # type: ignore[arg-type]
        tool_provider=selector,  # type: ignore[arg-type]
        embedding_provider=embeddings,  # type: ignore[arg-type]
    )

    vectors = asyncio.run(provider.get_embeddings(["processing-service"]))

    assert vectors == [[0.1, 0.2, 0.3]]
    assert embeddings.embedding_calls == [["processing-service"]]


def test_ollama_tool_provider_parses_native_api_chat_tool_calls(monkeypatch) -> None:
    provider = OllamaToolCallingProvider(
        model="qwen3.5:4b",
        base_url="http://ollama:11434",
    )
    captured: dict[str, Any] = {}

    async def fake_post_chat(payload: dict[str, Any]) -> dict[str, Any]:
        captured.update(payload)
        return {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "apm_mcp_ping",
                            "arguments": {},
                        }
                    }
                ],
            },
            "done": True,
        }

    monkeypatch.setattr(provider, "_post_chat", fake_post_chat)
    tool = LLMTool(
        name="apm_mcp_ping",
        description="Check MCP availability",
        parameters={"type": "object", "properties": {}},
        func=lambda: {"status": "ok"},
    )

    result = asyncio.run(
        provider.get_llm_response(
            _context(),
            tools=[tool],
            conversation_id="incident-1",
        )
    )

    assert provider.endpoint == "http://ollama:11434/api/chat"
    assert captured["model"] == "qwen3.5:4b"
    assert captured["stream"] is False
    assert captured["think"] is False
    assert captured["tools"][0]["function"]["name"] == "apm_mcp_ping"
    assert result["tool_calls"][0]["name"] == "apm_mcp_ping"
    assert result["tool_calls"][0]["arguments"] == {}


def test_hybrid_provider_uses_project_model_roles_from_runtime_config() -> None:
    config = SimpleNamespace(
        reasoning_model="gemma4:e2b",
        tool_model="qwen3.5:4b",
        embedding_model="ibm/granite-embedding:30m",
        ollama_url="http://ollama:11434",
    )

    provider = HybridLLMProvider.from_runtime(config)

    assert provider.reasoning_model == "ollama/gemma4:e2b"
    assert provider.tool_model == "ollama_native/qwen3.5:4b"
    assert provider.embedding_model == "ollama/ibm/granite-embedding:30m"
    assert MCP_SERVER_NAME == "apm_mcp"
