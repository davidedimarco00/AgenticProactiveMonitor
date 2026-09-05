from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .models import RoleLLMProvider
from .react_contracts import (
    _ContextAwarePromptGemmaDiagnosticFinalizer,
    _StructuredReasoningDecision,
)
from .specialist_react import SpecialistReActExecutor as _ProductionSpecialistReActExecutor


def _configured_reasoning_temperature() -> float:
    """Sampling temperature used only by specialist reasoning/finalization tests."""

    raw = os.getenv("AGENT_REASONING_TEMPERATURE", "0").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("AGENT_REASONING_TEMPERATURE must be a number") from exc
    if not 0.0 <= value <= 2.0:
        raise RuntimeError("AGENT_REASONING_TEMPERATURE must be between 0 and 2")
    return value


class _EvaluationDiagnosticFinalizer(_ContextAwarePromptGemmaDiagnosticFinalizer):
    """Finalizer with an evaluation-controlled sampling temperature."""

    def _options(self) -> dict[str, Any]:
        options = super()._options()
        options["temperature"] = _configured_reasoning_temperature()
        return options


class SpecialistReActExecutor(_ProductionSpecialistReActExecutor):
    """Evaluation adapter that changes only specialist sampling temperature.

    With AGENT_REASONING_TEMPERATURE=0 this is behaviourally equivalent to the
    production specialist reasoning/finalization path. The evaluation harness can
    raise the value without changing Technical Lead or Qwen tool-selection sampling.
    """

    async def _native_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> Any:
        if not isinstance(self.reasoning_provider, RoleLLMProvider):
            return await super()._native_reasoning_request(messages)

        schema = self._reasoning_json_schema()
        structured_messages = [dict(item) for item in messages]
        structured_messages.insert(
            1 if structured_messages else 0,
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object conforming to the following JSON Schema. "
                    "For gather_evidence, evidence_request is the binding semantic contract passed "
                    "to Qwen; do not name a tool. Do not add prose, Markdown fences, comments, or "
                    "extra fields. JSON Schema: "
                    + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        )
        context_size = self._ollama_context_size()
        payload = {
            "model": self._ollama_model_name(self.reasoning_provider),
            "messages": structured_messages,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {
                "temperature": _configured_reasoning_temperature(),
                "num_ctx": context_size,
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
                f"(content_chars={len(content)}, done_reason={done_reason}, num_ctx={context_size})"
            ) from exc

        decision = _StructuredReasoningDecision.model_validate(raw)
        if decision.action == "finish" and not self._reasoning_messages_have_live_evidence(messages):
            raise ValueError(
                "finish requires at least one successful live diagnostic observation; "
                "request structured evidence instead"
            )
        if decision.evidence_request is not None and self._active_reasoning_assignment is not None:
            normalized_request = self._normalize_and_validate_evidence_request(
                decision.evidence_request,
                self._active_reasoning_assignment,
            )
            decision = decision.model_copy(update={"evidence_request": normalized_request})
        return decision

    def _build_finalizer(self) -> _EvaluationDiagnosticFinalizer:
        return _EvaluationDiagnosticFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
            context_size=self._ollama_context_size(),
        )
