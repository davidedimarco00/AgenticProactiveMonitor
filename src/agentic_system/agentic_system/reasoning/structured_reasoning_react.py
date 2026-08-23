from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel
from spade_llm.context import ContextManager

from .langchain_agent import (
    ReActEvidence,
    ReActInvestigationError,
    ReActInvestigationResult,
    _ReasoningDecision,
)
from .models import RoleLLMProvider
from .observation_aware_react import SpecialistReActExecutor as _ObservationAwareExecutor


class SpecialistReActExecutor(_ObservationAwareExecutor):
    """Observation-aware ReAct with robust, bounded Gemma reasoning.

    Python keeps deterministic execution invariants such as structured output,
    duplicate-action protection and bounded recovery. Diagnostic semantics stay
    with the reasoning/finalization model: budget exhaustion or evidence-path
    saturation must not automatically upgrade ``inconclusive`` to ``probable``
    or suppress a model-requested cross-domain collaboration.

    Production ``RoleLLMProvider`` instances use Ollama's native JSON-schema
    endpoint for deterministic structured reasoning. Injected/test providers use
    the provider abstraction instead, so unit tests never depend on a live
    Ollama server and can still validate the same reasoning contract.
    """

    _DUPLICATE_SELECTION_MARKER = "same successful diagnostic call was already executed"
    _EMPTY_CAUSE_MARKERS = {"", "none", "null", "unknown", "unconfirmed", "n/a"}

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

    @classmethod
    def _is_duplicate_selection_error(cls, error: Exception) -> bool:
        return cls._DUPLICATE_SELECTION_MARKER in str(error).lower()

    @classmethod
    def _is_concrete_cause(cls, value: Any) -> bool:
        """Return whether text is non-empty enough to pass back to Gemma.

        This is deliberately NOT a diagnostic-validity check. Python does not
        decide whether the hypothesis is actually causal; the finalizer does.
        """

        return str(value or "").strip().lower() not in cls._EMPTY_CAUSE_MARKERS

    @classmethod
    def _best_available_hypothesis(cls, decisions: list[_ReasoningDecision]) -> str | None:
        for decision in reversed(decisions):
            hypothesis = str(decision.current_hypothesis or "").strip()
            if cls._is_concrete_cause(hypothesis):
                return hypothesis
        return None

    async def _provider_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> _ReasoningDecision:
        """Execute the structured reasoning contract through an injected provider.

        This path is primarily used by unit-test/fake providers. It deliberately
        preserves the same schema and normalization used by native Ollama.
        """

        system_prompt = ""
        context = ContextManager(system_prompt="")
        conversation_id = "specialist-native-structured-reasoning"
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            content = str(message.get("content") or "").strip()
            if role == "system" and not system_prompt:
                system_prompt = content
                continue
            context.add_message_dict(
                {"role": role or "user", "content": content},
                conversation_id,
            )
        if system_prompt:
            context = ContextManager(system_prompt=system_prompt)
            for message in messages:
                role = str(message.get("role") or "user").strip().lower()
                if role == "system":
                    continue
                context.add_message_dict(
                    {
                        "role": role or "user",
                        "content": str(message.get("content") or "").strip(),
                    },
                    conversation_id,
                )

        response = await self.reasoning_provider.get_llm_response(
            context,
            tools=None,
            conversation_id=conversation_id,
            output_schema=_ReasoningDecision,
        )
        structured = response.get("structured")
        if isinstance(structured, BaseModel):
            raw = structured.model_dump()
        elif isinstance(structured, dict):
            raw = dict(structured)
        else:
            text = str(response.get("text") or "").strip()
            if not text:
                raise RuntimeError("Injected reasoning provider returned no structured content")
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Injected reasoning provider returned invalid JSON") from exc

        normalized = self._normalize_reasoning_payload(raw)
        return _ReasoningDecision.model_validate(normalized)

    async def _native_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> _ReasoningDecision:
        # Production RoleLLMProvider instances use Ollama native structured JSON
        # because this path avoids the structured-output transport instability
        # previously observed through the generic SPADE-LLM/LiteLLM layer.
        # Test/injected providers must remain fully offline and therefore use
        # their normal provider abstraction instead of making a raw HTTP call.
        if not isinstance(self.reasoning_provider, RoleLLMProvider):
            return await self._provider_reasoning_request(messages)

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

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> _ReasoningDecision:
        decision = await super()._reason(
            assignment=assignment,
            evidence=evidence,
            decisions=decisions,
        )
        # Keep a bounded recovery snapshot so duplicate Qwen actions can end
        # cleanly without turning a technical action-selection problem into a
        # fabricated diagnostic status.
        self._bounded_evidence_snapshot = list(evidence)
        self._bounded_decisions_snapshot = [*decisions, decision]
        return decision

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
        self._bounded_evidence_snapshot: list[ReActEvidence] = []
        self._bounded_decisions_snapshot: list[_ReasoningDecision] = []

        try:
            return await super().investigate(
                task_id=task_id,
                incident_id=incident_id,
                agent_role=agent_role,
                severity=severity,
                entity=entity,
                anomaly=anomaly,
            )
        except ReActInvestigationError as exc:
            if not self._is_duplicate_selection_error(exc):
                raise

            evidence = list(self._bounded_evidence_snapshot)
            decisions = list(self._bounded_decisions_snapshot)
            if not evidence:
                raise

            assignment = {
                "task_id": task_id,
                "incident_id": incident_id,
                "agent_role": agent_role,
                "severity": severity,
                "entity": entity,
                "anomaly": anomaly,
            }
            best_hypothesis = self._best_available_hypothesis(decisions)
            decisions.append(
                _ReasoningDecision(
                    action="finish",
                    decision_summary=(
                        "The action selector cannot add a new discriminating observation without "
                        "repeating an already successful call. The local evidence path is saturated. "
                        "Finalize strictly from the retained evidence without changing diagnostic "
                        "semantics merely to close the bounded run. If an abnormal causal mechanism "
                        "is sufficiently supported, diagnose it. If a different specialist can test "
                        "a specific unresolved cross-domain hypothesis, request that assistance. "
                        "Otherwise a bounded inconclusive result is valid."
                    ),
                    current_hypothesis=best_hypothesis,
                    evidence_needed=None,
                )
            )

            await self._emit_trace(
                action="evidence_saturation",
                reason=(
                    "Qwen repeatedly selected an already successful equivalent diagnostic action; "
                    "the specialist will stop gathering local evidence and let Gemma finalize the "
                    "diagnostic meaning of the retained evidence."
                ),
                incident_id=incident_id,
                task_id=task_id,
                outcome="finalize_without_semantic_promotion",
                details={
                    "react_steps": min(len(decisions), self.max_steps),
                    "successful_evidence_count": sum(1 for item in evidence if item.success),
                    "selection_error": str(exc),
                },
            )

            try:
                output = await self._finalize(
                    assignment=assignment,
                    evidence=evidence,
                    decisions=decisions,
                )
            except ReActInvestigationError as finalization_error:
                if not self._is_semantic_closure_error(finalization_error):
                    raise
                # A finalizer/schema failure is a technical fallback, not evidence
                # for a probable diagnosis. Preserve a bounded inconclusive result.
                output = self._hard_stop_output(
                    evidence=evidence,
                    decisions=decisions,
                    reason=str(finalization_error),
                )

            await self._emit_trace(
                action="diagnosis",
                reason=output.summary,
                incident_id=incident_id,
                task_id=task_id,
                outcome=(
                    f"status={output.diagnosis_status}; confidence={output.confidence:.3f}; "
                    f"root_cause={output.root_cause or 'unconfirmed'}"
                ),
                details={
                    **output.model_dump(),
                    "closure_trigger": "evidence_saturation",
                    "semantic_promotion": False,
                },
            )

            conversation_id = f"react:{agent_role.strip().lower()}:{incident_id}:{task_id}"
            self.context.add_assistant_message(output.model_dump_json(), conversation_id)

            tools_used: list[str] = []
            for item in evidence:
                if item.tool not in tools_used:
                    tools_used.append(item.tool)

            return ReActInvestigationResult(
                task_id=task_id,
                incident_id=incident_id,
                agent_role=agent_role.strip().lower(),
                summary=output.summary,
                diagnosis_status=output.diagnosis_status,
                root_cause=output.root_cause,
                causal_chain=tuple(output.causal_chain),
                confidence=output.confidence,
                findings=tuple(output.findings),
                evidence=tuple(item.to_dict() for item in evidence),
                hypotheses=tuple(output.hypotheses),
                recommended_next_steps=tuple(output.recommended_next_steps),
                assistance_required=output.assistance_required,
                assistance_domain=output.assistance_domain,
                react_steps=min(max(len(decisions), 1), self.max_steps),
                tools_used=tuple(tools_used),
                conversation_id=conversation_id,
            )