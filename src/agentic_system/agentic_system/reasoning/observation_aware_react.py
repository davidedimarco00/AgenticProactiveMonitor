from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .langchain_agent import ReActEvidence
from .schema_validated_react import SpecialistReActExecutor as _SchemaValidatedExecutor


_LIST_LIMITS: dict[str, int] = {
    "processes": 12,
    "threads": 16,
    "connections": 20,
    "results": 8,
    "logs": 20,
    "metrics": 20,
    "addresses": 20,
}
_DEFAULT_LIST_LIMIT = 16
_MAX_REASONING_STRING_CHARS = 3000


@dataclass(frozen=True, slots=True)
class ObservationAwareEvidence(ReActEvidence):
    """One tool observation with separate audit and LLM-facing representations.

    `observation` is the complete normalized MCP result retained for audit,
    persistence and operator inspection. `reasoning_observation` is a bounded,
    structurally compact projection used by Gemma in subsequent ReAct steps.
    """

    reasoning_observation: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "observation": self.observation,
            "reasoning_observation": self.reasoning_observation,
            "success": self.success,
        }

    def to_reasoning_evidence(self) -> ReActEvidence:
        return ReActEvidence(
            step=self.step,
            tool=self.tool,
            arguments=dict(self.arguments),
            observation=self.reasoning_observation,
            success=self.success,
        )


class SpecialistReActExecutor(_SchemaValidatedExecutor):
    """ReAct executor with loss-aware Act -> Observe -> Reason propagation.

    MCP output is preserved in full for the audit trail while Gemma receives a
    compact structured projection. This avoids blind character-prefix truncation
    while keeping reasoning prompts bounded and useful.
    """

    def _project_evidence_for_reasoning(
        self,
        evidence: list[ReActEvidence],
    ) -> list[ReActEvidence]:
        projected: list[ReActEvidence] = []
        for item in evidence:
            if isinstance(item, ObservationAwareEvidence):
                projected.append(item.to_reasoning_evidence())
            else:
                projected.append(item)
        return projected

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> Any:
        return await super()._reason(
            assignment=assignment,
            evidence=self._project_evidence_for_reasoning(evidence),
            decisions=decisions,
        )

    async def _finalize(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> Any:
        return await super()._finalize(
            assignment=assignment,
            evidence=self._project_evidence_for_reasoning(evidence),
            decisions=decisions,
        )

    async def _execute_tool(
        self,
        *,
        step: int,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ObservationAwareEvidence:
        tool = self._langchain_tool_by_name[tool_name]
        raw = await tool.ainvoke(arguments)
        wrapper = self._decode_tool_result(raw)

        wrapper_success = bool(wrapper.get("success", False))
        if wrapper_success:
            raw_observation = self._normalize_raw_observation(wrapper.get("observation"))
        else:
            raw_observation = {
                "status": "error",
                "error": str(wrapper.get("error") or "Tool execution failed"),
            }

        success = wrapper_success and not self._observation_reports_error(raw_observation)
        reasoning_observation = self._reasoning_projection(
            raw_observation,
            tool_name=tool_name,
            success=success,
        )

        # Used only to enrich the immediately following operator trace event.
        self._latest_observation_trace = {
            "tool": tool_name,
            "raw_observation": raw_observation,
            "reasoning_observation": reasoning_observation,
            "success": success,
        }

        return ObservationAwareEvidence(
            step=step,
            tool=tool_name,
            arguments=dict(arguments),
            observation=raw_observation,
            reasoning_observation=reasoning_observation,
            success=success,
        )

    async def _emit_trace(
        self,
        *,
        action: str,
        reason: str,
        incident_id: str,
        task_id: str,
        outcome: str,
        tool: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        enriched = dict(details or {})
        latest = getattr(self, "_latest_observation_trace", None)
        if (
            action in {"observe", "rag_retrieval"}
            and isinstance(latest, dict)
            and tool
            and latest.get("tool") == tool
        ):
            enriched.pop("observation", None)
            enriched.update(
                {
                    "raw_observation": latest.get("raw_observation"),
                    "reasoning_observation": latest.get("reasoning_observation"),
                    "success": bool(latest.get("success")),
                    "observation_contract": (
                        "raw_observation is retained for audit; reasoning_observation is the "
                        "structured view supplied to Gemma."
                    ),
                }
            )

        await super()._emit_trace(
            action=action,
            reason=reason,
            incident_id=incident_id,
            task_id=task_id,
            outcome=outcome,
            tool=tool,
            details=enriched,
        )

    @staticmethod
    def _normalize_raw_observation(value: Any) -> Any:
        """Make an MCP result JSON-safe without discarding diagnostic fields."""
        try:
            return json.loads(json.dumps(value, default=str, ensure_ascii=False))
        except (TypeError, ValueError, json.JSONDecodeError):
            return str(value)

    @staticmethod
    def _observation_reports_error(observation: Any) -> bool:
        if not isinstance(observation, dict):
            return False
        status = str(observation.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure"}:
            return True
        if observation.get("success") is False:
            return True
        return False

    @classmethod
    def _reasoning_projection(
        cls,
        observation: Any,
        *,
        tool_name: str,
        success: bool,
    ) -> Any:
        if not success:
            if isinstance(observation, dict):
                return {
                    "status": "error",
                    "error": str(observation.get("error") or "Diagnostic action failed"),
                    "tool": tool_name,
                }
            return {"status": "error", "error": str(observation), "tool": tool_name}

        compact = cls._compact_value(observation)
        if isinstance(compact, dict):
            compact = dict(compact)
            compact.setdefault("_observation_view", "reasoning_projection")
            compact.setdefault("_source_tool", tool_name)
        return compact

    @classmethod
    def _compact_value(cls, value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {
                str(item_key): cls._compact_value(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }

        if isinstance(value, list):
            limit = _LIST_LIMITS.get(str(key or "").lower(), _DEFAULT_LIST_LIMIT)
            selected = [cls._compact_value(item) for item in value[:limit]]
            if len(value) > limit:
                return {
                    "items": selected,
                    "returned_to_reasoning": len(selected),
                    "total_items": len(value),
                    "omitted_items": len(value) - len(selected),
                }
            return selected

        if isinstance(value, str) and len(value) > _MAX_REASONING_STRING_CHARS:
            return {
                "content": value[:_MAX_REASONING_STRING_CHARS],
                "original_chars": len(value),
                "truncated_for_reasoning": True,
            }

        return value
