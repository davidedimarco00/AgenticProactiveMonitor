from __future__ import annotations

import asyncio
from typing import Any

import pytest

import agentic_system.reasoning.langchain_agent as langchain_agent
from agentic_system.reasoning.langchain_agent import _OllamaSchemaFinalizer


VALID_RESULT = {
    "summary": "Live network evidence supports a confirmed latency explanation.",
    "diagnosis_status": "confirmed",
    "root_cause": "Observed network latency explains the detector anomaly.",
    "causal_chain": [
        "Latency increased on the observed communication path.",
        "The detector observed the elevated service latency.",
    ],
    "confidence": 0.91,
    "findings": ["The network measurement is elevated."],
    "hypotheses": [],
    "recommended_next_steps": [],
    "assistance_required": False,
    "assistance_domain": None,
}


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        import json

        return {"message": {"content": json.dumps(VALID_RESULT), "thinking": ""}}


class FakeAsyncClient:
    captured: dict[str, Any] = {}

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> FakeResponse:
        type(self).captured = {"url": url, "json": json, "timeout": self.timeout}
        return FakeResponse()


def test_native_ollama_finalizer_uses_gemma_without_thinking_and_sends_json_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(langchain_agent.httpx, "AsyncClient", FakeAsyncClient)
    finalizer = _OllamaSchemaFinalizer(
        model="gemma4:e2b",
        base_url="http://127.0.0.1:11434",
        timeout_seconds=120.0,
    )

    result = asyncio.run(
        finalizer.ainvoke(
            [
                {"role": "system", "content": "Serialize diagnostic evidence."},
                {"role": "user", "content": "Use only collected evidence."},
            ]
        )
    )

    payload = FakeAsyncClient.captured["json"]
    assert FakeAsyncClient.captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert payload["model"] == "gemma4:e2b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["format"]["type"] == "object"
    assert "diagnosis_status" in payload["format"]["properties"]
    assert any("JSON Schema" in message["content"] for message in payload["messages"])
    assert result.diagnosis_status == "confirmed"
    assert result.assistance_required is False


def test_native_ollama_finalizer_rejects_empty_final_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyResponse(FakeResponse):
        def json(self) -> dict[str, Any]:
            return {"message": {"content": "", "thinking": "reasoning only"}}

    class EmptyClient(FakeAsyncClient):
        async def post(self, url: str, *, json: dict[str, Any]) -> EmptyResponse:
            return EmptyResponse()

    monkeypatch.setattr(langchain_agent.httpx, "AsyncClient", EmptyClient)
    finalizer = _OllamaSchemaFinalizer(
        model="gemma4:e2b",
        base_url="http://127.0.0.1:11434",
        timeout_seconds=120.0,
    )

    with pytest.raises(RuntimeError, match="empty content"):
        asyncio.run(finalizer.ainvoke([{"role": "user", "content": "Finalize."}]))
