from __future__ import annotations

import json
from typing import Any

import httpx

from .langchain_agent import _ReasoningDecision
from .observation_aware_react import SpecialistReActExecutor as _ObservationAwareExecutor


class SpecialistReActExecutor(_ObservationAwareExecutor):
    """Observation-aware ReAct with robust native Gemma decision normalization.

    Ollama can satisfy the generated JSON Schema while still omitting
    ``evidence_needed`` because that conditional requirement is enforced by a
    Pydantic model validator rather than represented directly in the schema.
    This executor makes the field schema-required and deterministically repairs
    a missing evidence request from Gemma's own operational decision text before
    validating the decision object.
    """

    @staticmethod
    def _reasoning_json_schema() -> dict[str, Any]:
        schema = _ReasoningDecision.model_json_schema()
        required = list(schema.get("required") or [])
        if "evidence_needed" not in required:
            required.append("evidence_needed")
        schema["required"] = required
        return schema

    @staticmethod
    def _normalize_reasoning_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Gemma reasoning output must be a JSON object")

        normalized = dict(payload)
        action = str(normalized.get("action") or "").strip()

        if action == "gather_evidence":
            evidence_needed = str(normalized.get("evidence_needed") or "").strip()
            if not evidence_needed:
                decision_summary = str(normalized.get("decision_summary") or "").strip()
                current_hypothesis = str(normalized.get("current_hypothesis") or "").strip()
                if decision_summary:
                    evidence_needed = decision_summary
                elif current_hypothesis:
                    evidence_needed = (
                        "Collect live evidence that materially tests this hypothesis: "
                        f"{current_hypothesis}"
                    )
                else:
                    evidence_needed = (
                        "Collect one live observation that materially tests the current "
                        "assignment before diagnostic closure."
                    )
                normalized["evidence_needed"] = evidence_needed
        elif action == "finish":
            normalized["evidence_needed"] = None

        return normalized

    async def _native_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> _ReasoningDecision:
        schema = self._reasoning_json_schema()
        payload = {
            "model": self._ollama_model_name(self.reasoning_provider),
            "messages": messages,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0},
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
            raise RuntimeError("Ollama reasoning step returned invalid JSON") from exc

        normalized = self._normalize_reasoning_payload(raw)
        return _ReasoningDecision.model_validate(normalized)
