from __future__ import annotations

import json
import os
from typing import Any, Literal

import httpx
from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from .langchain_agent import AssistanceDomain, DiagnosisStatus, ReasoningAction


_DEFAULT_OLLAMA_CONTEXT = 8192

EvidenceKind = Literal[
    "metric_history",
    "runtime_resource_state",
    "process_attribution",
    "process_detail",
    "log_evidence",
    "network_path",
    "application_endpoint",
    "storage_state",
    "static_knowledge",
]
EvidenceTimeScope = Literal["anomaly_window", "recent_history", "current", "static"]
EvidenceCausalRelation = Literal[
    "measure_signal",
    "attribute_cause",
    "test_hypothesis",
    "temporal_compare",
    "static_context",
    "cross_domain_hypothesis",
]


def _configured_ollama_context() -> int:
    """Return the context size used by native Ollama reasoning/finalization."""

    raw = os.getenv("AGENT_LLM_CONTEXT", str(_DEFAULT_OLLAMA_CONTEXT)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("AGENT_LLM_CONTEXT must be an integer") from exc
    if value <= 0:
        raise RuntimeError("AGENT_LLM_CONTEXT must be greater than zero")
    return value


class _EvidenceRequest(BaseModel):
    """Semantic contract between Gemma reasoning and Qwen action selection.

    Gemma decides the evidence family and diagnostic purpose without naming a
    concrete MCP tool. Qwen remains responsible for selecting one compatible
    bound tool and valid arguments. The incident signal itself is not an LLM
    field; the runtime attaches it from the deterministic incident anchor.
    """

    kind: EvidenceKind
    target_component: str
    related_component: str | None = None
    purpose: str
    time_scope: EvidenceTimeScope
    causal_relation: EvidenceCausalRelation
    causal_link: str

    @field_validator("target_component", "purpose", "causal_link")
    @classmethod
    def _required_text_not_empty(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("evidence request text fields cannot be empty")
        return normalized

    @field_validator("related_component", mode="before")
    @classmethod
    def _normalize_related_component(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if normalized.lower() in {"", "none", "null", "n/a"}:
            return None
        return normalized

    @model_validator(mode="after")
    def _validate_static_semantics(self) -> "_EvidenceRequest":
        if self.kind == "static_knowledge":
            if self.time_scope != "static":
                raise ValueError("static_knowledge requires time_scope=static")
            if self.causal_relation != "static_context":
                raise ValueError("static_knowledge requires causal_relation=static_context")
        elif self.time_scope == "static" or self.causal_relation == "static_context":
            raise ValueError(
                "runtime evidence requests cannot use static time/context semantics"
            )
        return self


class _StructuredReasoningDecision(BaseModel):
    """Production Gemma reasoning contract with an explicit evidence request.

    ``evidence_needed`` remains available as a computed compatibility view for
    the lower-level ReAct loop, but it is derived from the structured request
    rather than being a second free-text decision channel.
    """

    action: ReasoningAction
    decision_summary: str
    current_hypothesis: str | None = None
    evidence_request: _EvidenceRequest | None

    @computed_field(return_type=str | None)
    @property
    def evidence_needed(self) -> str | None:
        if self.evidence_request is None:
            return None
        return self.evidence_request.purpose

    @field_validator("decision_summary")
    @classmethod
    def _decision_summary_not_empty(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("decision_summary cannot be empty")
        return normalized

    @field_validator("current_hypothesis", mode="before")
    @classmethod
    def _normalize_hypothesis(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if normalized.lower() in {"", "none", "null", "unknown", "unconfirmed"}:
            return None
        return normalized

    @model_validator(mode="after")
    def _validate_action_contract(self) -> "_StructuredReasoningDecision":
        if self.action == "gather_evidence":
            if self.evidence_request is None:
                raise ValueError("gather_evidence requires evidence_request")
        elif self.evidence_request is not None:
            raise ValueError("finish requires evidence_request=null")
        return self


class _PromptDiagnosticFinalOutput(BaseModel):
    """Single diagnostic output contract used by every specialist.

    The LLM decides only ``assistance_domain``. ``assistance_required`` is
    derived deterministically so contradictory collaboration states cannot be
    emitted by the model.
    """

    summary: str
    diagnosis_status: DiagnosisStatus
    root_cause: str | None = None
    causal_chain: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[str]
    hypotheses: list[str]
    recommended_next_steps: list[str]
    assistance_domain: AssistanceDomain | None = None

    @computed_field(return_type=bool)
    @property
    def assistance_required(self) -> bool:
        return self.assistance_domain is not None

    @field_validator("summary")
    @classmethod
    def _summary_not_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("summary cannot be empty")
        return normalized

    @field_validator("root_cause", mode="before")
    @classmethod
    def _normalize_root_cause(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if normalized.lower() in {"", "none", "null", "unknown", "unconfirmed"}:
            return None
        return normalized

    @field_validator("causal_chain", "findings", "hypotheses", "recommended_next_steps")
    @classmethod
    def _clean_lists(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _validate_closure(self) -> "_PromptDiagnosticFinalOutput":
        if self.diagnosis_status in {"confirmed", "probable"}:
            if not self.root_cause:
                raise ValueError(f"{self.diagnosis_status} diagnosis requires a root_cause")
            if not self.causal_chain:
                raise ValueError(f"{self.diagnosis_status} diagnosis requires a causal_chain")

        if self.diagnosis_status == "confirmed" and self.assistance_domain is not None:
            raise ValueError("confirmed diagnosis cannot request diagnostic peer assistance")
        return self


class _PromptGemmaDiagnosticFinalizer:
    """Compatibility finalizer using the shared specialist output contract."""

    def __init__(self, *, model: str, base_url: str, timeout_seconds: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.schema = _PromptDiagnosticFinalOutput.model_json_schema(mode="validation")

    def _normalized_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
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
        return normalized

    def _options(self) -> dict[str, Any]:
        return {"temperature": 0}

    async def ainvoke(self, messages: list[dict[str, Any]]) -> _PromptDiagnosticFinalOutput:
        payload = {
            "model": self.model,
            "messages": self._normalized_messages(messages),
            "stream": False,
            "think": False,
            "format": self.schema,
            "options": self._options(),
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
        return _PromptDiagnosticFinalOutput.model_validate_json(content)


class _ContextAwarePromptGemmaDiagnosticFinalizer(_PromptGemmaDiagnosticFinalizer):
    """Production finalizer with explicit context and precise schema feedback."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        timeout_seconds: float,
        context_size: int,
    ) -> None:
        super().__init__(model=model, base_url=base_url, timeout_seconds=timeout_seconds)
        if context_size <= 0:
            raise ValueError("context_size must be greater than zero")
        self.context_size = context_size

    def _options(self) -> dict[str, Any]:
        return {"temperature": 0, "num_ctx": self.context_size}

    async def ainvoke(self, messages: list[dict[str, Any]]) -> _PromptDiagnosticFinalOutput:
        payload = {
            "model": self.model,
            "messages": self._normalized_messages(messages),
            "stream": False,
            "think": False,
            "format": self.schema,
            "options": self._options(),
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

        # Syntax failures are transport/serialization failures. Semantic schema
        # failures intentionally remain Pydantic ValidationError so the bounded
        # repair loop can return the exact rejection reason to Gemma.
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
