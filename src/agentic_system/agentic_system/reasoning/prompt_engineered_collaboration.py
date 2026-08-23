from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, computed_field, field_validator, model_validator

from .diagnostic_react import _ROLE_DOMAIN
from .langchain_agent import AssistanceDomain, DiagnosisStatus, ReActEvidence, ReActInvestigationError
from .prompt_engineered_react import SpecialistReActExecutor as _PromptEngineeredExecutor


class _PromptDiagnosticFinalOutput(BaseModel):
    """LLM-facing diagnostic contract with one source of truth for peer assistance.

    Gemma decides only ``assistance_domain``. ``assistance_required`` is derived
    deterministically so the model cannot emit contradictory combinations such
    as ``assistance_required=true`` with ``assistance_domain=null``.
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
        value = value.strip()
        if not value:
            raise ValueError("summary cannot be empty")
        return value

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
    def __init__(self, *, model: str, base_url: str, timeout_seconds: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Validation schema intentionally excludes the computed assistance_required field.
        self.schema = _PromptDiagnosticFinalOutput.model_json_schema(mode="validation")

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
                    "Return only an object conforming to this JSON Schema. "
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
            "options": {"temperature": 0},
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


class SpecialistReActExecutor(_PromptEngineeredExecutor):
    """Prompt-engineered executor with a non-contradictory collaboration contract."""

    FINALIZATION_POLICY = (
        _PromptEngineeredExecutor.FINALIZATION_POLICY
        + """

Peer-assistance output contract:
- Decide ONLY assistance_domain. Use system, network, application, software, or null.
- Do NOT output assistance_required; the runtime derives it from assistance_domain.
- A non-null assistance_domain means peer evidence is required.
- assistance_domain=null means no peer is required.
- For confirmed diagnosis assistance_domain MUST be null.
"""
    ).strip()

    def _build_finalizer(self) -> _PromptGemmaDiagnosticFinalizer:
        return _PromptGemmaDiagnosticFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
        )

    async def _finalize(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> _PromptDiagnosticFinalOutput:
        prompt = (
            f"{self.FINALIZATION_POLICY}\n\n"
            "Assignment:\n"
            f"{json.dumps(assignment, default=str, ensure_ascii=False)}\n\n"
            "Operational reasoning summaries:\n"
            f"{json.dumps([item.model_dump() for item in decisions], default=str, ensure_ascii=False)}\n\n"
            "Collected tool evidence:\n"
            f"{json.dumps([item.to_dict() for item in evidence], default=str, ensure_ascii=False)}"
        )
        current_domain = _ROLE_DOMAIN.get(str(assignment.get("agent_role") or "").strip().lower())

        last_error: Exception | None = None
        validation_feedback = ""
        for attempt in range(3):
            final_prompt = prompt
            if validation_feedback:
                final_prompt += (
                    "\n\nThe previous structured result was rejected. Correct ONLY the final "
                    "diagnostic object using the same evidence; do not gather or invent evidence. "
                    f"Validation feedback: {validation_feedback}"
                )
            try:
                structured = await self._invoke_with_provider_slot(
                    self.reasoning_provider,
                    self._finalizer.ainvoke(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "You are Gemma, the evidence-backed diagnostic finalization "
                                    "stage. Do not call tools and do not invent observations."
                                ),
                            },
                            {"role": "user", "content": final_prompt},
                        ]
                    ),
                )

                if isinstance(structured, _PromptDiagnosticFinalOutput):
                    output = structured
                elif isinstance(structured, BaseModel):
                    output = _PromptDiagnosticFinalOutput.model_validate(structured.model_dump())
                else:
                    output = _PromptDiagnosticFinalOutput.model_validate(structured)

                if current_domain and output.assistance_domain == current_domain:
                    raise ValueError(
                        f"{assignment.get('agent_role')} cannot request assistance from its own "
                        f"domain {current_domain!r}; choose a different peer domain or null"
                    )
                return output
            except asyncio.CancelledError:
                raise
            except ValidationError as exc:
                last_error = exc
                validation_feedback = "; ".join(
                    str(item.get("msg") or item) for item in exc.errors()
                )
            except Exception as exc:
                last_error = exc
                validation_feedback = str(exc)

        raise ReActInvestigationError(
            "Gemma structured diagnostic finalization failed: "
            f"{validation_feedback or last_error}"
        ) from last_error
