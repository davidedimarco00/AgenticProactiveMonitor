from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .models import RoleLLMProvider
from .react_contracts import (
    _ContextAwarePromptGemmaDiagnosticFinalizer,
    _PromptDiagnosticFinalOutput,
    _StructuredReasoningDecision,
)
from .specialist_react import SpecialistReActExecutor as _ProductionSpecialistReActExecutor


LOGGER = logging.getLogger("agentic_system.reasoning.evaluation_react")


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
    """Evaluation finalizer with bounded input and controlled sampling.

    The Ollama context stays fixed (8192 by default). The finalizer reserves the
    configured structured-output budget and an additional safety margin, then
    deterministically compacts only the LLM-facing copy of assignment, reasoning
    summaries and evidence. Complete MCP observations remain untouched in the
    persisted audit trail.
    """

    _SAFETY_MARGIN_TOKENS = 2048
    _GUARD_CHARS_PER_TOKEN = 1.5
    _ASSIGNMENT_MARKER = (
        "\n\nAssignment (including incident anchor and static grounding when available):\n"
    )
    _DECISIONS_MARKER = "\n\nOperational reasoning summaries:\n"
    _EVIDENCE_MARKER = "\n\nCollected tool evidence:\n"
    _IMPORTANT_OBSERVATION_KEYS = (
        "measurement_name",
        "field",
        "feature_field",
        "unit",
        "scope",
        "detector_aligned",
        "host_id",
        "target_host",
        "source",
        "target",
        "status",
        "reachable",
        "connect_ms",
        "latency_ms",
        "response_time",
        "latest",
        "average",
        "minimum",
        "maximum",
        "min",
        "max",
        "count",
        "metrics",
        "values",
        "samples",
        "results",
        "connections",
        "processes",
        "logs",
        "error",
    )

    def _options(self) -> dict[str, Any]:
        options = super()._options()
        options["temperature"] = _configured_reasoning_temperature()
        return options

    def _input_budget_tokens(self) -> int:
        output_reserve = int(self._options().get("num_predict") or 0)
        available = self.context_size - output_reserve - self._SAFETY_MARGIN_TOKENS
        if available < 1024:
            raise RuntimeError(
                "Configured Ollama context is too small for the evaluation finalizer: "
                f"num_ctx={self.context_size}, output_reserve={output_reserve}, "
                f"safety_margin={self._SAFETY_MARGIN_TOKENS}"
            )
        return available

    def _max_input_chars(self) -> int:
        # Ollama does not expose a tokenizer-only endpoint. The evaluation uses
        # a deliberately conservative character guard plus a 2048-token safety
        # margin. The actual prompt_eval_count is logged after every call.
        return int(self._input_budget_tokens() * self._GUARD_CHARS_PER_TOKEN)

    @staticmethod
    def _bounded_string(value: Any, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return text[:limit] + f"...<omitted {omitted} chars>"

    @classmethod
    def _select_sequence(cls, values: list[Any], limit: int) -> tuple[list[Any], int]:
        if len(values) <= limit:
            return list(values), 0
        head = max(1, (limit + 1) // 2)
        tail = max(0, limit - head)
        selected = list(values[:head])
        if tail:
            selected.extend(values[-tail:])
        return selected, len(values) - len(selected)

    @classmethod
    def _compact_value(
        cls,
        value: Any,
        *,
        list_limit: int,
        string_limit: int,
        dict_limit: int,
        depth: int = 0,
    ) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls._bounded_string(value, string_limit)
        if depth >= 4:
            if isinstance(value, dict):
                return {"summary": f"dict with {len(value)} keys omitted at depth limit"}
            if isinstance(value, (list, tuple)):
                return {"summary": f"sequence with {len(value)} items omitted at depth limit"}
            return cls._bounded_string(value, string_limit)

        if isinstance(value, (list, tuple)):
            raw_values = list(value)
            selected, omitted = cls._select_sequence(raw_values, list_limit)
            compacted = [
                cls._compact_value(
                    item,
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_limit=dict_limit,
                    depth=depth + 1,
                )
                for item in selected
            ]
            if omitted:
                return {
                    "items": compacted,
                    "total_items": len(raw_values),
                    "omitted_items": omitted,
                }
            return compacted

        if isinstance(value, dict):
            keys: list[str] = []
            for key in cls._IMPORTANT_OBSERVATION_KEYS:
                if key in value and key not in keys:
                    keys.append(key)
            for raw_key in value:
                key = str(raw_key)
                if key not in keys:
                    keys.append(key)
            selected_keys = keys[:dict_limit]
            result = {
                key: cls._compact_value(
                    value[key],
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_limit=dict_limit,
                    depth=depth + 1,
                )
                for key in selected_keys
                if key in value
            }
            omitted = len(keys) - len(selected_keys)
            if omitted > 0:
                result["omitted_keys"] = omitted
            return result

        return cls._bounded_string(value, string_limit)

    @classmethod
    def _compact_assignment(cls, assignment: dict[str, Any], level: int) -> dict[str, Any]:
        string_limit = (600, 420, 280, 180)[level]
        list_limit = (4, 3, 2, 2)[level]
        dict_limit = (18, 14, 10, 8)[level]
        projected: dict[str, Any] = {}
        for key in ("task_id", "incident_id", "agent_role", "severity", "entity"):
            if key in assignment:
                projected[key] = assignment[key]

        anchor = assignment.get("incident_anchor")
        if isinstance(anchor, dict):
            projected["incident_anchor"] = cls._compact_value(
                anchor,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_limit=dict_limit,
            )

        anomaly = assignment.get("anomaly")
        if isinstance(anomaly, dict):
            anomaly_keys = (
                "detector_name",
                "detector_type",
                "measurement_name",
                "feature_name",
                "feature_field",
                "affected_component",
                "data_start_time",
                "data_end_time",
                "anomaly_grade",
                "confidence",
                "feature_value",
            )
            projected["anomaly"] = {
                key: cls._compact_value(
                    anomaly[key],
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_limit=dict_limit,
                )
                for key in anomaly_keys
                if key in anomaly
            }

        grounding = assignment.get("static_project_grounding")
        if grounding is not None and level < 3:
            projected["static_project_grounding"] = cls._compact_value(
                grounding,
                list_limit=2 if level == 0 else 1,
                string_limit=360 if level == 0 else 220,
                dict_limit=10 if level == 0 else 7,
            )
        return projected

    @classmethod
    def _compact_decisions(cls, decisions: list[Any], level: int) -> list[dict[str, Any]]:
        max_items = (8, 6, 4, 3)[level]
        selected, omitted = cls._select_sequence(decisions, max_items)
        string_limit = (500, 360, 260, 180)[level]
        compacted: list[dict[str, Any]] = []
        for raw in selected:
            item = dict(raw) if isinstance(raw, dict) else {}
            projected: dict[str, Any] = {}
            for key in ("action", "decision_summary", "current_hypothesis", "evidence_needed"):
                if key in item:
                    value = item.get(key)
                    projected[key] = (
                        cls._bounded_string(value, string_limit)
                        if isinstance(value, str)
                        else value
                    )
            request = item.get("evidence_request")
            if isinstance(request, dict):
                projected["evidence_request"] = {
                    key: cls._bounded_string(request.get(key), string_limit)
                    if isinstance(request.get(key), str)
                    else request.get(key)
                    for key in (
                        "kind",
                        "target_component",
                        "related_component",
                        "purpose",
                        "time_scope",
                        "causal_relation",
                        "causal_link",
                    )
                    if key in request
                }
            compacted.append(projected)
        if omitted:
            compacted.insert(
                len(compacted) // 2,
                {"omitted_reasoning_summaries": omitted},
            )
        return compacted

    @classmethod
    def _compact_evidence(cls, evidence: list[Any], level: int) -> list[dict[str, Any]]:
        max_items = (8, 7, 6, 5)[level]
        selected, omitted = cls._select_sequence(evidence, max_items)
        list_limit = (4, 3, 2, 2)[level]
        string_limit = (520, 360, 240, 160)[level]
        dict_limit = (16, 12, 9, 7)[level]
        compacted: list[dict[str, Any]] = []
        for raw in selected:
            item = dict(raw) if isinstance(raw, dict) else {}
            compacted.append(
                {
                    "step": item.get("step"),
                    "tool": item.get("tool"),
                    "arguments": cls._compact_value(
                        item.get("arguments") or {},
                        list_limit=list_limit,
                        string_limit=string_limit,
                        dict_limit=dict_limit,
                    ),
                    "observation": cls._compact_value(
                        item.get("observation"),
                        list_limit=list_limit,
                        string_limit=string_limit,
                        dict_limit=dict_limit,
                    ),
                    "success": bool(item.get("success")),
                }
            )
        if omitted:
            compacted.insert(
                len(compacted) // 2,
                {"omitted_evidence_items": omitted},
            )
        return compacted

    @classmethod
    def _parse_finalization_prompt(
        cls,
        content: str,
    ) -> tuple[str, dict[str, Any], list[Any], list[Any]] | None:
        if cls._ASSIGNMENT_MARKER not in content:
            return None
        policy, remainder = content.split(cls._ASSIGNMENT_MARKER, 1)
        if cls._DECISIONS_MARKER not in remainder:
            return None
        assignment_text, remainder = remainder.split(cls._DECISIONS_MARKER, 1)
        if cls._EVIDENCE_MARKER not in remainder:
            return None
        decisions_text, evidence_text = remainder.split(cls._EVIDENCE_MARKER, 1)
        try:
            assignment = json.loads(assignment_text)
            decisions = json.loads(decisions_text)
            evidence = json.loads(evidence_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(assignment, dict) or not isinstance(decisions, list) or not isinstance(evidence, list):
            return None
        return policy, assignment, decisions, evidence

    @classmethod
    def _render_compact_prompt(
        cls,
        parsed: tuple[str, dict[str, Any], list[Any], list[Any]],
        level: int,
    ) -> str:
        policy, assignment, decisions, evidence = parsed
        return (
            policy
            + cls._ASSIGNMENT_MARKER
            + json.dumps(
                cls._compact_assignment(assignment, level),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + cls._DECISIONS_MARKER
            + json.dumps(
                cls._compact_decisions(decisions, level),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + cls._EVIDENCE_MARKER
            + json.dumps(
                cls._compact_evidence(evidence, level),
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )

    def _input_chars_after_schema_injection(self, messages: list[dict[str, Any]]) -> int:
        normalized = self._normalized_messages(messages)
        return sum(len(str(item.get("content") or "")) for item in normalized)

    def _prepare_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        prepared = [
            {
                "role": str(item.get("role") or "user").strip() or "user",
                "content": str(item.get("content") or "").strip(),
            }
            for item in messages
        ]
        user_index = next(
            (
                index
                for index in range(len(prepared) - 1, -1, -1)
                if prepared[index]["role"].lower() == "user"
            ),
            None,
        )
        max_chars = self._max_input_chars()
        original_chars = self._input_chars_after_schema_injection(prepared)
        if original_chars <= max_chars:
            return prepared, {
                "compacted": False,
                "compaction_level": None,
                "input_chars": original_chars,
                "input_char_guard": max_chars,
                "input_budget_tokens": self._input_budget_tokens(),
            }
        if user_index is None:
            raise RuntimeError("Finalizer input exceeds context guard and has no user prompt to compact")

        parsed = self._parse_finalization_prompt(prepared[user_index]["content"])
        if parsed is None:
            raise RuntimeError(
                "Finalizer input exceeds context guard and the structured finalization prompt "
                "could not be parsed safely; refusing raw text truncation"
            )

        for level in range(4):
            candidate = [dict(item) for item in prepared]
            candidate[user_index]["content"] = self._render_compact_prompt(parsed, level)
            candidate_chars = self._input_chars_after_schema_injection(candidate)
            if candidate_chars <= max_chars:
                return candidate, {
                    "compacted": True,
                    "compaction_level": level,
                    "input_chars": candidate_chars,
                    "input_char_guard": max_chars,
                    "input_budget_tokens": self._input_budget_tokens(),
                }

        raise RuntimeError(
            "Structured finalizer input cannot fit the deterministic context guard without "
            "discarding required diagnostic structure: "
            f"num_ctx={self.context_size}, input_char_guard={max_chars}"
        )

    async def ainvoke(self, messages: list[dict[str, Any]]) -> _PromptDiagnosticFinalOutput:
        prepared_messages, budget = self._prepare_messages(messages)
        normalized_messages = self._normalized_messages(prepared_messages)
        options = self._options()
        payload = {
            "model": self.model,
            "messages": normalized_messages,
            "stream": False,
            "think": False,
            "format": self.schema,
            "options": options,
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

        done_reason = str(body.get("done_reason") or "unknown")
        prompt_eval_count = body.get("prompt_eval_count")
        eval_count = body.get("eval_count")
        LOGGER.info(
            "Evaluation finalizer context: num_ctx=%s input_budget_tokens=%s input_chars=%s "
            "input_char_guard=%s compacted=%s level=%s prompt_eval_count=%s "
            "num_predict=%s eval_count=%s done_reason=%s",
            self.context_size,
            budget["input_budget_tokens"],
            budget["input_chars"],
            budget["input_char_guard"],
            budget["compacted"],
            budget["compaction_level"],
            prompt_eval_count,
            options.get("num_predict"),
            eval_count,
            done_reason,
        )

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Ollama diagnostic finalizer returned invalid JSON syntax "
                f"(content_chars={len(content)}, done_reason={done_reason}, "
                f"prompt_eval_count={prompt_eval_count}, eval_count={eval_count}, "
                f"num_ctx={self.context_size}, input_chars={budget['input_chars']}, "
                f"input_char_guard={budget['input_char_guard']})"
            ) from exc

        return _PromptDiagnosticFinalOutput.model_validate(raw)


class SpecialistReActExecutor(_ProductionSpecialistReActExecutor):
    """Evaluation adapter for controlled specialist configuration experiments.

    With AGENT_REASONING_TEMPERATURE=0 the specialist reasoning/finalization path
    remains equivalent to production sampling. The adapter also exposes the
    detector-aligned NETLAT metric name to the existing deterministic metric
    binding logic so a metric_history request on a NETLAT incident can call the
    same get_metrics tool used for CPU/RAM without asking Qwen to invent the
    measurement name.
    """

    @staticmethod
    def _metric_name_from_assignment(assignment: dict[str, Any]) -> str | None:
        anchor = assignment.get("incident_anchor")
        if isinstance(anchor, dict):
            signal = str(anchor.get("observed_signal") or "").strip().lower()
            if signal == "network_transport_latency":
                return "network_transport_latency"
        return _ProductionSpecialistReActExecutor._metric_name_from_assignment(assignment)

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
