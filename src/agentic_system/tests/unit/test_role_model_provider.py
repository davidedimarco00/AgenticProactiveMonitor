from __future__ import annotations

import asyncio

from spade_llm.context import ContextManager

import agentic_system.reasoning.models as models_module
from agentic_system.reasoning.models import RoleLLMProvider, SharedInferenceGate


class FakeLLMProvider:
    def __init__(self, *, model: str, base_url: str) -> None:
        self.model = model
        self.base_url = base_url
        self.chat_calls: list[dict[str, object]] = []
        self.embedding_calls: list[list[str]] = []

    async def get_llm_response(
        self,
        context,
        tools=None,
        conversation_id=None,
        output_schema=None,
    ):
        self.chat_calls.append(
            {
                "tools": tools,
                "conversation_id": conversation_id,
                "output_schema": output_schema,
            }
        )
        return {"text": "ok", "tool_calls": [], "structured": None}

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.embedding_calls.append(list(texts))
        return [[0.1, 0.2] for _ in texts]


def test_role_provider_uses_one_chat_model_and_separate_embedding_model(monkeypatch) -> None:
    monkeypatch.setattr(models_module, "LLMProvider", FakeLLMProvider)

    provider = RoleLLMProvider(
        model="qwen3.5:4b",
        base_url="http://ollama:11434",
        embedding_model="ibm/granite-embedding:30m",
        gate=SharedInferenceGate(1),
    )

    context = ContextManager(system_prompt="You are a test specialist.")
    response = asyncio.run(provider.get_llm_response(context))
    embeddings = asyncio.run(provider.get_embeddings(["incident evidence"]))

    assert response["text"] == "ok"
    assert provider.model == "ollama/qwen3.5:4b"
    assert provider.embedding_model == "ollama/ibm/granite-embedding:30m"
    assert provider._chat.chat_calls == [
        {"tools": None, "conversation_id": None, "output_schema": None}
    ]
    assert provider._embedding.chat_calls == []
    assert provider._embedding.embedding_calls == [["incident evidence"]]
    assert embeddings == [[0.1, 0.2]]


def test_role_providers_can_share_one_backend_inference_gate(monkeypatch) -> None:
    monkeypatch.setattr(models_module, "LLMProvider", FakeLLMProvider)

    gate = SharedInferenceGate(1)
    technical_lead = RoleLLMProvider(
        model="gemma4:e2b",
        base_url="http://ollama:11434",
        embedding_model="ibm/granite-embedding:30m",
        gate=gate,
    )
    specialist = RoleLLMProvider(
        model="qwen3.5:4b",
        base_url="http://ollama:11434",
        embedding_model="ibm/granite-embedding:30m",
        gate=gate,
    )

    assert technical_lead.model == "ollama/gemma4:e2b"
    assert specialist.model == "ollama/qwen3.5:4b"
    assert technical_lead.concurrency_snapshot()["max_concurrency"] == 1
    assert specialist.concurrency_snapshot()["scope"] == "BACKEND_GLOBAL"
