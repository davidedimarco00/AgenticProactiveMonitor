from __future__ import annotations

import json
from typing import Any

import httpx

from .diagnostic_react import _DiagnosticFinalOutput
from .langchain_agent import (
    ReActEvidence,
    ReActInvestigationError,
    ReActInvestigationResult,
    _ReasoningDecision,
)
from .observation_aware_react import SpecialistReActExecutor as _ObservationAwareExecutor


class SpecialistReActExecutor(_ObservationAwareExecutor):
    """Observation-aware ReAct with robust, bounded Gemma reasoning.

    Besides normalizing Gemma's structured decisions, this executor treats a
    repeatedly selected already-successful diagnostic action as evidence
    saturation rather than as a specialist failure. Once the bounded evidence
    budget is exhausted, it also guarantees a diagnosis-first closure: when
    live observations support a concrete causal hypothesis, the result is
    reported as probable instead of being discarded as inconclusive only
    because the ultimate source-level cause was not proven.
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
        return str(value or "").strip().lower() not in cls._EMPTY_CAUSE_MARKERS

    @classmethod
    def _best_available_hypothesis(cls, decisions: list[_ReasoningDecision]) -> str | None:
        for decision in reversed(decisions):
            hypothesis = str(decision.current_hypothesis or "").strip()
            if cls._is_concrete_cause(hypothesis):
                return hypothesis
        return None

    @classmethod
    def _best_diagnostic_candidate(
        cls,
        decisions: list[_ReasoningDecision],
        output: _DiagnosticFinalOutput | None = None,
    ) -> str | None:
        candidate = cls._best_available_hypothesis(decisions)
        if candidate:
            return candidate
        if output is not None:
            for hypothesis in output.hypotheses:
                candidate = str(hypothesis or "").strip()
                if cls._is_concrete_cause(candidate):
                    return candidate
        return None

    @staticmethod
    def _has_successful_live_evidence(evidence: list[ReActEvidence]) -> bool:
        return any(
            item.success and "search_knowledge" not in item.tool.lower()
            for item in evidence
        )

    @staticmethod
    def _is_bounded_closure(decisions: list[_ReasoningDecision], max_steps: int) -> bool:
        if len(decisions) >= max_steps:
            return True
        if not decisions:
            return False
        summary = str(decisions[-1].decision_summary or "").lower()
        return "evidence path is saturated" in summary

    @classmethod
    def _promote_bounded_best_effort(
        cls,
        *,
        output: _DiagnosticFinalOutput,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[_ReasoningDecision],
    ) -> _DiagnosticFinalOutput:
        """Turn a supported bounded hypothesis into a real probable diagnosis.

        The model is allowed to be uncertain about the ultimate source-code or
        business-level cause. It is not allowed to throw away a lower-level
        cause that is already supported by live evidence. For example, several
        Python worker processes consuming nearly one CPU each are a valid
        process-level explanation for a CPU anomaly even if the exact code path
        inside those workers is not yet known.
        """

        if output.diagnosis_status != "inconclusive" or output.assistance_required:
            return output
        if not cls._has_successful_live_evidence(evidence):
            return output

        candidate = cls._best_diagnostic_candidate(decisions, output)
        if not candidate:
            return output

        causal_chain = [str(item).strip() for item in output.causal_chain if str(item).strip()]
        entity = str(assignment.get("entity") or "affected entity").strip() or "affected entity"
        if not causal_chain:
            causal_chain = [
                candidate,
                (
                    f"The collected live observations on {entity} are consistent with this "
                    "mechanism causing the detected anomaly."
                ),
            ]

        hypotheses = [str(item).strip() for item in output.hypotheses if str(item).strip()]
        if candidate not in hypotheses:
            hypotheses.insert(0, candidate)

        findings = [str(item).strip() for item in output.findings if str(item).strip()]
        next_steps = [
            str(item).strip() for item in output.recommended_next_steps if str(item).strip()
        ]

        summary = str(output.summary or "").strip()
        if not summary:
            summary = f"Live evidence supports a probable cause for the anomaly on {entity}."

        confidence = min(max(float(output.confidence), 0.5), 1.0)
        return _DiagnosticFinalOutput(
            summary=summary,
            diagnosis_status="probable",
            root_cause=candidate,
            causal_chain=causal_chain,
            confidence=confidence,
            findings=findings,
            hypotheses=hypotheses,
            recommended_next_steps=next_steps,
            assistance_required=False,
            assistance_domain=None,
        )

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
        # Keep a bounded recovery snapshot so a duplicate Qwen action can be
        # converted into best-effort diagnostic closure instead of task failure.
        self._bounded_evidence_snapshot = list(evidence)
        self._bounded_decisions_snapshot = [*decisions, decision]
        return decision

    async def _finalize(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> _DiagnosticFinalOutput:
        output = await super()._finalize(
            assignment=assignment,
            evidence=evidence,
            decisions=decisions,
        )
        if output.diagnosis_status != "inconclusive":
            return output

        typed_decisions = [item for item in decisions if isinstance(item, _ReasoningDecision)]
        if not self._has_successful_live_evidence(evidence):
            return output
        if not self._is_bounded_closure(typed_decisions, self.max_steps):
            return output

        best_hypothesis = self._best_diagnostic_candidate(typed_decisions, output)
        if not best_hypothesis:
            return output

        reconsideration = _ReasoningDecision(
            action="finish",
            decision_summary=(
                "The bounded autonomous ReAct budget is exhausted and live diagnostic evidence "
                "supports a concrete causal hypothesis. Diagnose at the deepest level actually "
                "supported by the observations. Do not require an ultimate source-code cause when "
                "a process, component, dependency or runtime mechanism already explains the "
                "detected anomaly. If every causal link is not proven, report probable rather than "
                "inconclusive. Do not invent any missing fact."
            ),
            current_hypothesis=best_hypothesis,
            evidence_needed=None,
        )
        reconsidered = await super()._finalize(
            assignment=assignment,
            evidence=evidence,
            decisions=[*typed_decisions, reconsideration],
        )
        if reconsidered.diagnosis_status != "inconclusive":
            return reconsidered

        # Deterministic safety net: a bounded investigation with live evidence
        # and a concrete causal hypothesis must return a diagnosis instead of
        # being lost because the model remains overly conservative.
        return self._promote_bounded_best_effort(
            output=reconsidered,
            assignment=assignment,
            evidence=evidence,
            decisions=[*typed_decisions, reconsideration],
        )

    @classmethod
    def _hard_stop_output(
        cls,
        *,
        evidence: list[ReActEvidence],
        decisions: list[_ReasoningDecision],
        reason: str,
    ) -> _DiagnosticFinalOutput:
        base = super()._hard_stop_output(
            evidence=evidence,
            decisions=decisions,
            reason=reason,
        )
        candidate = cls._best_diagnostic_candidate(decisions, base)
        if not candidate or not cls._has_successful_live_evidence(evidence):
            return base

        causal_chain = [
            candidate,
            "The retained live diagnostic observations are consistent with this mechanism causing the reported anomaly.",
        ]
        findings = [
            f"Successful live evidence was collected through {item.tool}."
            for item in evidence
            if item.success and "search_knowledge" not in item.tool.lower()
        ]
        return _DiagnosticFinalOutput(
            summary=(
                "The bounded investigation ended before the ultimate cause could be proven, but "
                "the retained live evidence supports a concrete probable diagnosis."
            ),
            diagnosis_status="probable",
            root_cause=candidate,
            causal_chain=causal_chain,
            confidence=0.5,
            findings=findings,
            hypotheses=[candidate],
            recommended_next_steps=[
                "Use the retained evidence to verify the remaining causal link; no automatic remediation is executed."
            ],
            assistance_required=False,
            assistance_domain=None,
        )

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
                        "repeating an already successful call. The autonomous evidence path is "
                        "saturated, so close now with the best diagnosis supported by the evidence. "
                        "If a concrete causal hypothesis is supported but not fully proven, report "
                        "it as probable instead of requesting another local diagnostic cycle."
                    ),
                    current_hypothesis=best_hypothesis,
                    evidence_needed=None,
                )
            )

            await self._emit_trace(
                action="evidence_saturation",
                reason=(
                    "Qwen repeatedly selected an already successful equivalent diagnostic action; "
                    "the specialist will stop gathering local evidence and finalize the best "
                    "supported diagnosis."
                ),
                incident_id=incident_id,
                task_id=task_id,
                outcome="finalize_best_available_evidence",
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
