from __future__ import annotations

from typing import Any

from .evaluation_react import (
    SpecialistReActExecutor as _EvaluationSpecialistReActExecutor,
    _EvaluationDiagnosticFinalizer as _BaseEvaluationDiagnosticFinalizer,
)


class _EvaluationDiagnosticFinalizer(_BaseEvaluationDiagnosticFinalizer):
    """Keep legacy finalizer prompts unchanged unless they exceed the guard.

    ``format=self.schema`` already sends the complete JSON Schema to Ollama.
    The historical finalizer also embeds the same schema verbatim in a system
    message. That duplication is intentionally preserved for prompts that fit
    the evaluation guard so existing CPU/RAM behaviour is not changed.

    When the complete normalized prompt would exceed the deterministic input
    guard, only the redundant textual schema copy is replaced by a concise
    instruction. The schema itself is still supplied through Ollama's ``format``
    field, while the structured assignment/evidence compaction implemented by
    ``_EvaluationDiagnosticFinalizer`` remains responsible for reducing dynamic
    incident context.
    """

    _COMPACT_SCHEMA_INSTRUCTION = (
        "Return only one JSON object matching the response schema supplied through the structured "
        "output format. Do not add prose, Markdown fences, comments, or extra fields. Peer "
        "assistance is represented only by assistance_domain: use system, network, application, "
        "software, or null. Do not output assistance_required."
    )

    @staticmethod
    def _plain_normalized_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        return [
            {
                "role": str(item.get("role") or "user").strip() or "user",
                "content": str(item.get("content") or "").strip(),
            }
            for item in messages
        ]

    def _normalized_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        # First preserve the exact historical prompt whenever it already fits.
        full = super()._normalized_messages(messages)
        full_chars = sum(len(str(item.get("content") or "")) for item in full)
        if full_chars <= self._max_input_chars():
            return full

        # For oversized prompts, avoid paying for the schema twice. The complete
        # schema is still sent in payload["format"], so this removes redundancy
        # rather than weakening the structured-output contract.
        compact = self._plain_normalized_messages(messages)
        compact.insert(
            1 if compact else 0,
            {
                "role": "system",
                "content": self._COMPACT_SCHEMA_INSTRUCTION,
            },
        )
        return compact


class SpecialistReActExecutor(_EvaluationSpecialistReActExecutor):
    """Evaluation executor using the context-guarded diagnostic finalizer."""

    def _build_finalizer(self) -> _EvaluationDiagnosticFinalizer:
        return _EvaluationDiagnosticFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
            context_size=self._ollama_context_size(),
        )


__all__ = ["SpecialistReActExecutor", "_EvaluationDiagnosticFinalizer"]
