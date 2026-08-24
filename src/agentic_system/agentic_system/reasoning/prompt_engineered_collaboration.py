from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, computed_field, field_validator, model_validator

from .diagnostic_react import _ROLE_DOMAIN
from .langchain_agent import (
    AssistanceDomain,
    DiagnosisStatus,
    ReActEvidence,
    ReActInvestigationError,
    ReActInvestigationResult,
)
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
    """Prompt-engineered executor with RAG grounding and bounded collaboration.

    Every investigation performs one deterministic, read-only Qdrant retrieval before
    ReAct step 1 when ``search_knowledge`` is available. The retrieved chunks are static
    project context, not live incident evidence, and therefore do not consume the bounded
    ReAct evidence budget. The normal RAG tool remains available for additional targeted
    retrieval if a new static knowledge gap emerges during reasoning.
    """

    INITIAL_RAG_LIMIT = 4
    _STATIC_GROUNDING_KEY = "static_project_grounding"

    TOOL_SELECTION_POLICY = (
        _PromptEngineeredExecutor.TOOL_SELECTION_POLICY.replace(
            "DNS/TCP/HTTP connectivity",
            "DNS/ICMP/TCP/HTTP connectivity",
        )
        + """

Initial RAG grounding policy:
- The assignment may already contain static_project_grounding retrieved from Qdrant before step 1.
- Use that grounding for known monitored-system topology, dependencies, configuration, telemetry
  semantics, endpoint meaning, expected behaviour and relevant runbooks.
- Do not spend a ReAct step repeating a broad knowledge search when the initial grounding already
  answers the static question. search_knowledge remains available for a NEW, specific static gap.
- Static grounding never proves that a runtime condition is currently true; select a live tool when
  Gemma asks for current-state evidence.
"""
    ).strip()

    REASONING_POLICY = (
        _PromptEngineeredExecutor.REASONING_POLICY
        .replace(
            "sockets, DNS/TCP/HTTP\nconnectivity between monitored services",
            "sockets, DNS/ICMP/TCP/HTTP\nconnectivity between monitored services",
        )
        .replace(
            "network =\n  DNS/TCP/sockets/routes/connectivity/network latency",
            "network =\n  DNS/ICMP/TCP/sockets/routes/connectivity/network latency",
        )
        + """

Initial RAG grounding policy:
- Before the first bounded ReAct step, the runtime may attach static_project_grounding to the
  assignment. Read and USE it when forming hypotheses and interpreting live observations.
- Grounding can establish static project facts such as architecture, dependencies, configuration,
  telemetry semantics, expected service behaviour and runbook guidance.
- Grounding is NOT live evidence and cannot confirm that a fault is currently occurring. A current
  incident claim still requires live MCP/OpenSearch observations.
- Prefer using the already retrieved grounding before requesting another broad RAG lookup. Request
  search_knowledge during ReAct only when a new, specific project-fact gap remains.
"""
    ).strip()

    FINALIZATION_POLICY = (
        _PromptEngineeredExecutor.FINALIZATION_POLICY
        .replace("assistance_required=false.", "assistance_domain=null.")
        .replace("set assistance_required=true.", "set assistance_domain to that peer domain.")
        .replace("When assistance_required=true", "When assistance_domain is non-null")
        .replace(
            "assistance_required=false rather than inventing a cause.",
            "assistance_domain=null rather than inventing a cause.",
        )
        + """

Initial RAG grounding policy:
- static_project_grounding in the assignment is authoritative project context retrieved from Qdrant.
- Use it to interpret topology, dependencies, configuration, telemetry semantics, service behaviour
  and runbook expectations when building the causal explanation.
- Do NOT treat static grounding as proof of a current runtime failure. confirmed/probable incident
  mechanisms must still be supported by the collected live observations required by the policies above.

Peer-assistance output contract:
- Decide ONLY assistance_domain. Use system, network, application, software, or null.
- Do NOT output assistance_required; the runtime derives it from assistance_domain.
- A non-null assistance_domain means peer evidence is required.
- assistance_domain=null means no peer is required.
- For confirmed diagnosis assistance_domain MUST be null.
"""
    ).strip()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Task-scoped storage avoids mixing grounding between concurrent incidents.
        self._initial_rag_context_by_task: dict[str, dict[str, Any]] = {}

    @classmethod
    def _build_initial_rag_query(
        cls,
        *,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> str:
        """Build a deterministic project-grounding query without LLM-authored facts."""

        anomaly_snapshot = json.dumps(
            anomaly,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(anomaly_snapshot) > 1100:
            anomaly_snapshot = anomaly_snapshot[:1100] + "..."

        query = (
            "Retrieve authoritative monitored-system context for an incident investigation. "
            f"Specialist role: {agent_role.strip().lower()}. "
            f"Affected entity: {entity}. Severity: {severity}. "
            f"Anomaly metadata: {anomaly_snapshot}. "
            "Prioritize architecture, service dependencies, configuration, telemetry semantics, "
            "expected service behaviour, endpoint/request flow and relevant diagnostic runbooks. "
            "Return project-specific facts useful for interpreting later live evidence; do not "
            "infer current runtime state."
        )
        # search_knowledge accepts at most 2000 characters.
        return query[:2000]

    def _knowledge_tool_name(self) -> str | None:
        for name in sorted(self._tool_names):
            normalized = name.strip().lower()
            if normalized == "search_knowledge" or normalized.endswith("search_knowledge"):
                return name
        return None

    def _assignment_with_grounding(self, assignment: dict[str, Any]) -> dict[str, Any]:
        task_id = str(assignment.get("task_id") or "").strip()
        grounding = self._initial_rag_context_by_task.get(task_id)
        if not grounding:
            return assignment

        enriched = dict(assignment)
        enriched[self._STATIC_GROUNDING_KEY] = grounding
        return enriched

    async def _retrieve_initial_rag_grounding(
        self,
        *,
        task_id: str,
        incident_id: str,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> ReActEvidence | None:
        tool_name = self._knowledge_tool_name()
        if tool_name is None:
            await self._emit_trace(
                action="rag_context_grounding",
                reason="Initial RAG grounding skipped because search_knowledge is not available.",
                incident_id=incident_id,
                task_id=task_id,
                outcome="rag_tool_unavailable; continue_with_live_diagnostics",
                details={"react_budget_consumed": False},
            )
            return None

        query = self._build_initial_rag_query(
            agent_role=agent_role,
            severity=severity,
            entity=entity,
            anomaly=anomaly,
        )
        item = await self._execute_tool(
            step=0,
            tool_name=tool_name,
            arguments={"query": query, "limit": self.INITIAL_RAG_LIMIT},
        )

        returned_results = None
        if isinstance(item.observation, dict):
            raw_count = item.observation.get("returned_results")
            if isinstance(raw_count, int):
                returned_results = raw_count

        await self._emit_trace(
            action="rag_context_grounding",
            reason=(
                "Retrieved project-specific Qdrant context before ReAct step 1."
                if item.success
                else "Initial project grounding was unavailable; continue with live diagnostics."
            ),
            incident_id=incident_id,
            task_id=task_id,
            tool=tool_name,
            outcome=(
                f"success={str(item.success).lower()}; results={returned_results}; "
                "react_budget_consumed=false"
            ),
            details={
                "arguments": dict(item.arguments),
                "observation": item.observation,
                "success": item.success,
                "source": "Qdrant RAG",
                "evidence_kind": "static_project_grounding",
                "runtime_evidence": False,
                "react_budget_consumed": False,
            },
        )
        return item

    async def investigate(
        self,
        *,
        task_id: str,
        incident_id: str,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> ReActInvestigationResult:
        normalized_task_id = task_id.strip()
        grounding_item: ReActEvidence | None = None

        try:
            grounding_item = await self._retrieve_initial_rag_grounding(
                task_id=normalized_task_id,
                incident_id=incident_id.strip(),
                agent_role=agent_role,
                severity=severity,
                entity=entity,
                anomaly=anomaly,
            )

            if grounding_item is not None and grounding_item.success:
                self._initial_rag_context_by_task[normalized_task_id] = {
                    "source": "Qdrant RAG",
                    "tool": grounding_item.tool,
                    "runtime_evidence": False,
                    "usage_rule": (
                        "Static project context for hypothesis formation and interpretation only; "
                        "current incident claims require live evidence."
                    ),
                    "query": grounding_item.arguments.get("query"),
                    "retrieval": grounding_item.observation,
                }

            result = await super().investigate(
                task_id=task_id,
                incident_id=incident_id,
                agent_role=agent_role,
                severity=severity,
                entity=entity,
                anomaly=anomaly,
            )

            if grounding_item is None:
                return result

            grounding_record = grounding_item.to_dict()
            grounding_record.update(
                {
                    "source": "Qdrant RAG",
                    "evidence_kind": "static_project_grounding",
                    "runtime_evidence": False,
                    "react_budget_consumed": False,
                }
            )
            tools_used = result.tools_used
            if grounding_item.tool not in tools_used:
                tools_used = (grounding_item.tool, *tools_used)

            return replace(
                result,
                evidence=(grounding_record, *result.evidence),
                tools_used=tools_used,
            )
        finally:
            self._initial_rag_context_by_task.pop(normalized_task_id, None)

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> Any:
        return await super()._reason(
            assignment=self._assignment_with_grounding(assignment),
            evidence=evidence,
            decisions=decisions,
        )

    async def _select_tool(
        self,
        *,
        assignment: dict[str, Any],
        evidence_needed: str,
        evidence: list[ReActEvidence],
    ) -> tuple[str, dict[str, Any]]:
        return await super()._select_tool(
            assignment=self._assignment_with_grounding(assignment),
            evidence_needed=evidence_needed,
            evidence=evidence,
        )

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
        grounded_assignment = self._assignment_with_grounding(assignment)
        prompt = (
            f"{self.FINALIZATION_POLICY}\n\n"
            "Assignment (including static RAG grounding when available):\n"
            f"{json.dumps(grounded_assignment, default=str, ensure_ascii=False)}\n\n"
            "Operational reasoning summaries:\n"
            f"{json.dumps([item.model_dump() for item in decisions], default=str, ensure_ascii=False)}\n\n"
            "Collected live/tool evidence:\n"
            f"{json.dumps([item.to_dict() for item in evidence], default=str, ensure_ascii=False)}"
        )
        current_domain = _ROLE_DOMAIN.get(
            str(grounded_assignment.get("agent_role") or "").strip().lower()
        )

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
                        f"{grounded_assignment.get('agent_role')} cannot request assistance from its own "
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
