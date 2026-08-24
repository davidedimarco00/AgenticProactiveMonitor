from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

import agentic_system.reasoning.context_robust_react as module
from agentic_system.reasoning.context_robust_react import (
    SpecialistReActExecutor,
    _ContextAwarePromptGemmaDiagnosticFinalizer,
    _configured_ollama_context,
)
from agentic_system.reasoning.models import RoleLLMProvider


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _FakeAsyncClient:
    last_payload: dict | None = None
    response_body: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict):
        type(self).last_payload = json
        return _FakeResponse(type(self).response_body)


def _native_executor() -> SpecialistReActExecutor:
    executor = object.__new__(SpecialistReActExecutor)
    provider = object.__new__(RoleLLMProvider)
    provider.model = "ollama/gemma4:e4b"
    provider.base_url = "http://ollama:11434"
    executor.reasoning_provider = provider
    executor.tool_timeout_seconds = 30.0
    return executor


def _finalizer() -> _ContextAwarePromptGemmaDiagnosticFinalizer:
    return _ContextAwarePromptGemmaDiagnosticFinalizer(
        model="gemma4:e4b",
        base_url="http://ollama:11434",
        timeout_seconds=60.0,
        context_size=8192,
    )


def _finalizer_messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Finalize only from evidence."},
        {"role": "user", "content": "Evidence payload"},
    ]


def test_configured_ollama_context_defaults_and_accepts_override(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_CONTEXT", raising=False)
    assert _configured_ollama_context() == 8192

    monkeypatch.setenv("AGENT_LLM_CONTEXT", "12288")
    assert _configured_ollama_context() == 12288


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_configured_ollama_context_rejects_invalid_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("AGENT_LLM_CONTEXT", value)
    with pytest.raises(RuntimeError, match="AGENT_LLM_CONTEXT"):
        _configured_ollama_context()


def test_native_reasoning_sends_num_ctx_and_schema_in_prompt(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_CONTEXT", "8192")
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_payload = None
    _FakeAsyncClient.response_body = {
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "action": "gather_evidence",
                    "decision_summary": "Collect current CPU evidence.",
                    "current_hypothesis": "A CPU-bound workload may be saturating the service.",
                    "evidence_needed": "Current processing-service CPU usage.",
                }
            ),
        },
    }

    executor = _native_executor()
    decision = asyncio.run(
        executor._native_reasoning_request(
            [
                {"role": "system", "content": "Reasoning policy"},
                {"role": "user", "content": "Incident assignment"},
            ]
        )
    )

    assert decision.action == "gather_evidence"
    payload = _FakeAsyncClient.last_payload
    assert payload is not None
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["temperature"] == 0
    assert payload["format"]["type"] == "object"
    assert any(
        message["role"] == "system" and "JSON Schema" in message["content"]
        for message in payload["messages"]
    )


def test_diagnostic_finalizer_sends_same_context_size(monkeypatch) -> None:
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_payload = None
    _FakeAsyncClient.response_body = {
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "summary": "Evidence is insufficient for a concrete cause.",
                    "diagnosis_status": "inconclusive",
                    "root_cause": None,
                    "causal_chain": [],
                    "confidence": 0.2,
                    "findings": ["A live observation was collected."],
                    "hypotheses": ["A CPU-bound workload remains possible."],
                    "recommended_next_steps": [],
                    "assistance_domain": None,
                }
            ),
        },
    }

    result = asyncio.run(_finalizer().ainvoke(_finalizer_messages()))

    assert result.diagnosis_status == "inconclusive"
    payload = _FakeAsyncClient.last_payload
    assert payload is not None
    assert payload["options"]["num_ctx"] == 8192
    assert any("JSON Schema" in message["content"] for message in payload["messages"])


def test_diagnostic_finalizer_propagates_semantic_schema_validation(monkeypatch) -> None:
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.response_body = {
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "summary": "A specific cause is still not established.",
                    "diagnosis_status": "probable",
                    "root_cause": None,
                    "causal_chain": [],
                    "confidence": 0.4,
                    "findings": ["Live evidence was collected."],
                    "hypotheses": ["A CPU-bound workload remains possible."],
                    "recommended_next_steps": [],
                    "assistance_domain": None,
                }
            ),
        },
    }

    with pytest.raises(ValidationError, match="probable diagnosis requires a root_cause"):
        asyncio.run(_finalizer().ainvoke(_finalizer_messages()))


def test_diagnostic_finalizer_wraps_only_json_syntax_failure(monkeypatch) -> None:
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.response_body = {
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": '{"summary":"truncated"',
        },
    }

    with pytest.raises(RuntimeError, match="invalid JSON syntax") as exc_info:
        asyncio.run(_finalizer().ainvoke(_finalizer_messages()))

    message = str(exc_info.value)
    assert "done_reason=stop" in message
    assert "num_ctx=8192" in message
