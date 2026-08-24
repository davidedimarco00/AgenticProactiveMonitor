from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .langchain_agent import ReActInvestigationError, _ReasoningDecision
from .models import RoleLLMProvider
from .prompt_engineered_collaboration import (
    _PromptDiagnosticFinalOutput,
    _PromptGemmaDiagnosticFinalizer,
    SpecialistReActExecutor as _GroundedCollaborativeExecutor,
)


_DEFAULT_OLLAMA_CONTEXT = 8192


def _configured_ollama_context() -> int:
    """Return the bounded agent context configured for native Ollama requests.

    ``AGENT_LLM_CONTEXT`` is already passed to the agentic-backend container by
    Docker Compose. Native ``/api/chat`` calls must forward it explicitly as
    ``options.num_ctx``; otherwise Ollama may use its own default context size.
    """

    raw = os.getenv("AGENT_LLM_CONTEXT", str(_DEFAULT_OLLAMA_CONTEXT)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("AGENT_LLM_CONTEXT must be an integer") from exc
    if value <= 0:
        raise RuntimeError("AGENT_LLM_CONTEXT must be greater than zero")
    return value


class _ContextAwarePromptGemmaDiagnosticFinalizer(_PromptGemmaDiagnosticFinalizer):
    """Prompt finalizer that applies context while preserving schema feedback.

    JSON syntax errors are transport/serialization failures and are wrapped with
    bounded Ollama diagnostics. A syntactically valid JSON object that violates
    the diagnostic Pydantic contract is deliberately *not* wrapped: its
    ValidationError must propagate to the parent bounded repair loop so Gemma
    receives the exact semantic rejection reason and can correct only the final
    structured object using the same evidence.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        timeout_seconds: float,
        context_size: int,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        if context_size <= 0:
            raise ValueError("context_size must be greater than zero")
        self.context_size = context_size

    async def ainvoke(self, messages: list[dict[str, Any]]) -> _PromptDiagnosticFinalOutput:
        normalized = [
            {
                "role": str(item.get("role") or "user").strip() or "user",
                "content": str(item.get("content") or "").strip(),
            }
            for item in messages
        ]
        normalized.insert(
            1 if normalized else 0,
            {
                "role": "system",
                "content": (
                    "Return only an object conforming exactly to this JSON Schema. "
                    "Do not add prose, Markdown fences, comments, or fields outside the schema. "
                    "Peer assistance is represented ONLY by assistance_domain: use one of "
                    "system, network, application, software, or null. Do not output an "
                    "assistance_required field. JSON Schema: "
                    + json.dumps(self.schema, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        )
        payload = {
            "model": self.model,
            "messages": normalized,
            "stream": False,
            "think": False,
            "format": self.schema,
            "options": {
                "temperature": 0,
                "num_ctx": self.context_size,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()

        message = body.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Ollama diagnostic finalizer returned no message object")
        content = str(message.get("content") or "").strip()
        if not content:
            raise RuntimeError("Ollama diagnostic finalizer returned empty content")

        # Keep syntax and semantic/schema failures separate. The parent
        # collaboration finalizer already catches Pydantic ValidationError and
        # turns it into precise bounded repair feedback for the next Gemma
        # attempt. Wrapping that error here would hide the useful reason.
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            done_reason = str(body.get("done_reason") or "unknown")
            raise RuntimeError(
                "Ollama diagnostic finalizer returned invalid JSON syntax "
                f"(content_chars={len(content)}, done_reason={done_reason}, "
                f"num_ctx={self.context_size})"
            ) from exc

        return _PromptDiagnosticFinalOutput.model_validate(raw)


class SpecialistReActExecutor(_GroundedCollaborativeExecutor):
    """Grounded specialist executor with robust native Ollama structured output.

    The existing RAG grounding, bounded ReAct loop, collaboration policy and
    evidence semantics remain unchanged. This final layer only strengthens the
    native Gemma transport contract:

    - ``AGENT_LLM_CONTEXT`` is sent as Ollama ``num_ctx``;
    - the reasoning JSON Schema is included both in ``format`` and in the prompt;
    - malformed JSON is distinguished from schema-invalid diagnostic content;
    - diagnostic schema ValidationError reaches the existing bounded repair loop.
    """

    @staticmethod
    def _ollama_context_size() -> int:
        return _configured_ollama_context()

    async def _native_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> _ReasoningDecision:
        # Unit/injected providers stay fully offline and preserve the provider
        # abstraction used by the existing test suite.
        if not isinstance(self.reasoning_provider, RoleLLMProvider):
            return await self._provider_reasoning_request(messages)

        schema = self._reasoning_json_schema()
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        structured_messages = [dict(item) for item in messages]
        structured_messages.insert(
            1 if structured_messages else 0,
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object conforming to the following JSON Schema. "
                    "Do not add prose, Markdown fences, comments, or extra fields. JSON Schema: "
                    + schema_text
                ),
            },
        )

        payload = {
            "model": self._ollama_model_name(self.reasoning_provider),
            "messages": structured_messages,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {
                "temperature": 0,
                "num_ctx": self._ollama_context_size(),
            },
        }
        timeout = max(60.0, self.tool_timeout_seconds * 4)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._ollama_base_url(self.reasoning_provider)}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()

        message = body.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Ollama reasoning step returned no message object")
        content = str(message.get("content") or "").strip()
        if not content:
            raise RuntimeError("Ollama reasoning step returned empty content")

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            done_reason = str(body.get("done_reason") or "unknown")
            raise RuntimeError(
                "Ollama reasoning step returned invalid JSON "
                f"(content_chars={len(content)}, done_reason={done_reason}, "
                f"num_ctx={self._ollama_context_size()})"
            ) from exc

        normalized = self._normalize_reasoning_payload(raw)
        return _ReasoningDecision.model_validate(normalized)

    def _build_finalizer(self) -> _ContextAwarePromptGemmaDiagnosticFinalizer:
        return _ContextAwarePromptGemmaDiagnosticFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
            context_size=self._ollama_context_size(),
        )
