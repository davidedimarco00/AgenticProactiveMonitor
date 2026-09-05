from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from spade_llm.context import create_assistant_tool_call_message

from .diagnostic_react import _DiagnosticFinalOutput, _ROLE_GUIDANCE
from .react_contracts import UNCONFIRMED_ROOT_CAUSE
from .langchain_agent import (
    ReActEvidence,
    ReActInvestigationError,
    ReActInvestigationResult,
    _ReasoningDecision,
)
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
    """ReAct executor with loss-aware observations and guarded diagnostic closure.

    MCP output is preserved in full for the audit trail while Gemma receives a
    compact structured projection. Diagnostic closure is also guarded so that a
    premature ``finish`` decision or a schema-invalid final diagnosis does not
    automatically terminate an investigation while autonomous evidence budget
    remains available.
    """

    TOOL_SELECTION_POLICY = """
You are the action-selection component of an IT monitoring specialist agent. Gemma has already
specified WHAT evidence is needed. Your only task is to select HOW to collect that evidence.
Do not diagnose, explain, summarize or remediate. Call exactly ONE bound tool.

Selection policy:
1. Live runtime claims require live diagnostic evidence. Prefer the most specific MCP tool for
   metrics, logs, process/runtime state, disk state, sockets, DNS/TCP/HTTP connectivity or other
   observable telemetry requested by Gemma.
2. Use RAG/project knowledge only for static architecture, dependencies, configuration, runbooks
   and expected service behaviour. RAG/project knowledge can explain telemetry but cannot prove
   that a runtime condition is currently true.
3. Prefer a tool that can confirm or reject the current hypothesis with one read-only observation.
4. Do not repeat a successful equivalent call already present in previous_tool_calls unless the
   evidence request explicitly requires a temporal comparison.
5. Populate arguments only from the assignment, Gemma's evidence request and supplied context.
   Never invent host IDs, service names, ports, time windows, process IDs or other identifiers.
6. Choose the narrowest tool whose declared schema and description satisfy the evidence request.
7. If project knowledge is required to interpret live evidence, retrieve only the missing static
   fact; do not replace a live check with RAG.

Return no natural-language answer: produce exactly one schema-valid tool call.
""".strip()

    REASONING_POLICY = """
You are the reasoning component of an IT monitoring specialist agent.
AgentSpeak has already committed the investigation intention. You do NOT call tools and you do
NOT choose tool names. Return only a concise auditable operational decision, never private
chain-of-thought.

Safe diagnostic evidence that the action layer can collect includes current resource state,
process and thread state, process hierarchy and /proc metadata, disk state, sockets, DNS/TCP/HTTP
connectivity between monitored services, OpenSearch metrics/logs, and project knowledge via RAG.

Choose exactly one action:
- gather_evidence: whenever another safe live observation can materially confirm/reject the
  current hypothesis, identify a concrete causal mechanism, or distinguish between plausible causes.
- finish: only when at least one live observation exists AND either (a) a concrete evidence-backed
  root-cause hypothesis can be stated, or (b) the missing decisive evidence belongs to another
  specialist domain and must be requested explicitly.

A detector alert or symptom such as high CPU, high memory, latency, errors, or service degradation
is NOT by itself a root cause. Before choosing finish for a local diagnosis, current_hypothesis must
name the causal process, component, dependency, configuration/runtime condition, or other concrete
mechanism that explains the observed symptom. If you can only restate the anomaly, gather more
evidence instead.

Do not finish by merely recommending a diagnostic check that the action layer can perform now.
Do not invent evidence. Do not perform remediation. Prefer a small number of discriminating checks
over repeated equivalent checks. Request project/RAG knowledge when architecture, dependencies,
runbooks or service semantics are needed to interpret live telemetry.
""".strip()

    FINALIZATION_POLICY = """
Convert the completed investigation into the required diagnostic schema using only the supplied
assignment, operational reasoning summaries and collected tool evidence.

Evidence sufficiency:
- confirmed: root_cause and causal_chain are mandatory and must be directly supported by live
  observations. Static RAG knowledge alone cannot confirm a live incident. No peer assistance.
- probable: root_cause and causal_chain are mandatory. A probable diagnosis means a SPECIFIC
  evidence-backed causal hypothesis exists but at least one material causal link still needs
  confirmation. Never use probable to mean "the cause is unknown".
- inconclusive: use this status when no concrete root cause can yet be supported. root_cause may be
  null. Request peer assistance only when a different specialist domain can collect specific
  material evidence that is unavailable to the current specialist.

Output discipline:
- NEVER output confirmed or probable with root_cause=null, unknown, unconfirmed, or empty.
- NEVER output confirmed or probable without a non-empty causal_chain.
- Do not use the anomaly symptom itself (for example "high CPU" or "high latency") as root_cause.
- never request assistance from the same specialist domain.
- findings are observations supported by collected evidence, not interpretations presented as facts.
- hypotheses are unresolved causal possibilities.
- when assistance_required=true, the first recommended_next_steps item must state the specific
  evidence the peer should collect and why it would reduce uncertainty.
- recommended_next_steps contain diagnostic verification only, never remediation, and must not
  postpone a safe diagnostic check that the current action layer can already perform.
- never invent evidence, measurements, logs, architecture facts or remediation.
""".strip()

    @staticmethod
    def _has_concrete_hypothesis(decision: _ReasoningDecision) -> bool:
        hypothesis = str(decision.current_hypothesis or "").strip()
        return hypothesis.lower() not in {"", "none", "null", "unknown", "unconfirmed", "n/a"}

    @staticmethod
    def _is_semantic_closure_error(error: Exception) -> bool:
        text = str(error).lower()
        markers = (
            "requires a root_cause",
            "requires a causal_chain",
            "assistance_required=true requires assistance_domain",
            "assistance_domain must be null",
            "confirmed diagnosis cannot request",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _closure_feedback(
        *,
        hypothesis: str | None,
        reason: str,
    ) -> _ReasoningDecision:
        return _ReasoningDecision(
            action="gather_evidence",
            decision_summary=(
                "Diagnostic closure was rejected because the collected evidence does not yet "
                f"support a schema-valid causal diagnosis: {reason}"
            ),
            current_hypothesis=(str(hypothesis).strip() or None) if hypothesis is not None else None,
            evidence_needed=(
                "Collect one additional safe live observation that identifies or discriminates a "
                "concrete root cause rather than merely confirming the anomaly symptom."
            ),
        )

    @staticmethod
    def _hard_stop_output(
        *,
        evidence: list[ReActEvidence],
        decisions: list[_ReasoningDecision],
        reason: str,
    ) -> _DiagnosticFinalOutput:
        findings = [
            f"A successful diagnostic observation was collected through {item.tool}."
            for item in evidence
            if item.success
        ]
        hypotheses: list[str] = []
        for item in decisions:
            hypothesis = str(item.current_hypothesis or "").strip()
            if hypothesis and hypothesis not in hypotheses:
                hypotheses.append(hypothesis)
        return _DiagnosticFinalOutput(
            summary=(
                "Autonomous diagnostic evidence was collected, but a schema-valid root cause could "
                "not be established before the bounded ReAct investigation ended."
            ),
            diagnosis_status="inconclusive",
            # A stated outcome rather than an empty field: every other closure
            # path now reports a cause, and the operator report must not show a
            # blank one here.
            root_cause=UNCONFIRMED_ROOT_CAUSE,
            causal_chain=[],
            confidence=0.0,
            findings=findings,
            hypotheses=hypotheses,
            recommended_next_steps=[
                "Review the retained diagnostic evidence and extend the autonomous evidence path "
                f"for the unresolved gap. Closure reason: {reason}"
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
        """Run ReAct while treating diagnostic closure as a guarded checkpoint.

        A premature ``finish`` is converted back into evidence gathering when a
        concrete causal hypothesis is absent. Likewise, semantic finalization
        errors such as ``probable`` with a null root cause no longer fail the
        whole specialist task while additional autonomous steps remain.
        """

        task_id = task_id.strip()
        incident_id = incident_id.strip()
        agent_role = agent_role.strip().lower()
        if not task_id or not incident_id or not agent_role:
            raise ValueError("ReAct investigation requires task, incident and agent identity")

        assignment = {
            "task_id": task_id,
            "incident_id": incident_id,
            "agent_role": agent_role,
            "severity": severity,
            "entity": entity,
            "anomaly": anomaly,
        }
        conversation_id = f"react:{agent_role}:{incident_id}:{task_id}"
        self.context.add_message_dict(
            {
                "role": "user",
                "content": (
                    "Hybrid ReAct investigation started for assignment: "
                    + json.dumps(assignment, separators=(",", ":"), sort_keys=True)
                ),
            },
            conversation_id,
        )

        evidence: list[ReActEvidence] = []
        tools_used: list[str] = []
        decisions: list[_ReasoningDecision] = []
        output: _DiagnosticFinalOutput | None = None

        await self._emit_trace(
            action="react_started",
            reason="AgentSpeak committed the investigation intention; Gemma starts evidence planning.",
            incident_id=incident_id,
            task_id=task_id,
            outcome=(
                f"reasoning={self._ollama_model_name(self.reasoning_provider)}; "
                f"tool_selection={self._ollama_model_name(self.tool_provider)}"
            ),
        )

        for step in range(1, self.max_steps + 1):
            decision = await self._reason(
                assignment=assignment,
                evidence=evidence,
                decisions=decisions,
            )
            decisions.append(decision)
            await self._emit_trace(
                action="reason",
                reason=decision.decision_summary,
                incident_id=incident_id,
                task_id=task_id,
                outcome=(
                    f"action={decision.action}; hypothesis={decision.current_hypothesis or 'none'}; "
                    f"evidence_needed={decision.evidence_needed or 'none'}"
                ),
                details=decision.model_dump(),
            )
            self.context.add_assistant_message(
                json.dumps(
                    {
                        "stage": "reason",
                        "step": step,
                        **decision.model_dump(),
                    },
                    ensure_ascii=False,
                ),
                conversation_id,
            )

            if decision.action == "finish" and evidence:
                if not self._has_concrete_hypothesis(decision) and step < self.max_steps:
                    feedback = self._closure_feedback(
                        hypothesis=decision.current_hypothesis,
                        reason="no concrete root-cause hypothesis was stated",
                    )
                    decisions[-1] = feedback
                    await self._emit_trace(
                        action="diagnostic_closure_rejected",
                        reason=feedback.decision_summary,
                        incident_id=incident_id,
                        task_id=task_id,
                        outcome="continue_react",
                        details=feedback.model_dump(),
                    )
                    continue

                try:
                    candidate = await self._finalize(
                        assignment=assignment,
                        evidence=evidence,
                        decisions=decisions,
                    )
                except ReActInvestigationError as exc:
                    if self._is_semantic_closure_error(exc) and step < self.max_steps:
                        feedback = self._closure_feedback(
                            hypothesis=decision.current_hypothesis,
                            reason=str(exc),
                        )
                        decisions[-1] = feedback
                        await self._emit_trace(
                            action="diagnostic_closure_rejected",
                            reason=feedback.decision_summary,
                            incident_id=incident_id,
                            task_id=task_id,
                            outcome="continue_react",
                            details={
                                **feedback.model_dump(),
                                "finalization_error": str(exc),
                            },
                        )
                        continue
                    if self._is_semantic_closure_error(exc):
                        output = self._hard_stop_output(
                            evidence=evidence,
                            decisions=decisions,
                            reason=str(exc),
                        )
                        break
                    raise

                if (
                    candidate.diagnosis_status == "inconclusive"
                    and not candidate.assistance_required
                    and step < self.max_steps
                ):
                    feedback = self._closure_feedback(
                        hypothesis=decision.current_hypothesis,
                        reason=(
                            "finalization remained inconclusive while autonomous diagnostic steps "
                            "are still available"
                        ),
                    )
                    decisions[-1] = feedback
                    await self._emit_trace(
                        action="diagnostic_closure_rejected",
                        reason=feedback.decision_summary,
                        incident_id=incident_id,
                        task_id=task_id,
                        outcome="continue_react",
                        details={
                            **feedback.model_dump(),
                            "candidate_diagnosis": candidate.model_dump(),
                        },
                    )
                    continue

                output = candidate
                break

            evidence_needed = str(decision.evidence_needed or "").strip()
            if not evidence_needed:
                evidence_needed = (
                    "Collect one live observation that materially tests the current anomaly "
                    "before attempting diagnostic closure."
                )

            tool_name, arguments = await self._select_tool(
                assignment=assignment,
                evidence_needed=evidence_needed,
                evidence=evidence,
            )
            await self._emit_trace(
                action="select_tool",
                reason=evidence_needed,
                incident_id=incident_id,
                task_id=task_id,
                tool=tool_name,
                outcome="Qwen selected one MCP/RAG action.",
                details={"arguments": arguments},
            )

            item = await self._execute_tool(
                step=step,
                tool_name=tool_name,
                arguments=arguments,
            )
            evidence.append(item)
            if tool_name not in tools_used:
                tools_used.append(tool_name)

            call_id = f"{task_id}-step-{step}"
            self.context.add_message_dict(
                create_assistant_tool_call_message(
                    [{"id": call_id, "name": tool_name, "arguments": dict(arguments)}]
                ),
                conversation_id,
            )
            self.context.add_tool_result(
                tool_name,
                item.observation,
                call_id,
                conversation_id,
            )

            is_rag = "search_knowledge" in tool_name.lower()
            await self._emit_trace(
                action="rag_retrieval" if is_rag else "observe",
                reason=(
                    "Project knowledge was retrieved for the requested evidence."
                    if is_rag
                    else "Live MCP observation returned to Gemma for the next reasoning step."
                ),
                incident_id=incident_id,
                task_id=task_id,
                tool=tool_name,
                outcome=self._observation_summary(item.observation, success=item.success),
                details={
                    "arguments": dict(arguments),
                    "observation": item.observation,
                    "success": item.success,
                    "source": "Qdrant RAG" if is_rag else "MCP",
                },
            )

        if not evidence:
            raise ReActInvestigationError(
                f"No operational tool was selected within {self.max_steps} ReAct steps"
            )

        if output is None:
            try:
                output = await self._finalize(
                    assignment=assignment,
                    evidence=evidence,
                    decisions=decisions,
                )
            except ReActInvestigationError as exc:
                if not self._is_semantic_closure_error(exc):
                    raise
                output = self._hard_stop_output(
                    evidence=evidence,
                    decisions=decisions,
                    reason=str(exc),
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
            details=output.model_dump(),
        )
        self.context.add_assistant_message(output.model_dump_json(), conversation_id)

        return ReActInvestigationResult(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=agent_role,
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
            react_steps=max(len(decisions), 1),
            tools_used=tuple(tools_used),
            conversation_id=conversation_id,
        )

    def _adapt_tool(self, tool: Any) -> StructuredTool:
        """Adapt an MCP tool without applying the legacy 6k observation truncation."""

        async def execute_spade_tool(**kwargs: Any) -> str:
            try:
                raw = await asyncio.wait_for(
                    tool.execute(**kwargs),
                    timeout=self.tool_timeout_seconds,
                )
                payload = {
                    "success": True,
                    "observation": self._normalize_raw_observation(raw),
                }
            except asyncio.TimeoutError:
                payload = {
                    "success": False,
                    "error": (
                        f"Tool {tool.name} exceeded timeout "
                        f"{self.tool_timeout_seconds:.1f}s"
                    ),
                }
            except Exception as exc:
                payload = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return json.dumps(payload, default=str, ensure_ascii=False)

        return StructuredTool.from_function(
            coroutine=execute_spade_tool,
            name=tool.name,
            description=tool.description,
            args_schema=tool.parameters,
            infer_schema=False,
        )

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

    async def _native_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> _ReasoningDecision:
        schema = _ReasoningDecision.model_json_schema()
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
        return _ReasoningDecision.model_validate_json(content)

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> _ReasoningDecision:
        projected = self._project_evidence_for_reasoning(evidence)
        role = str(assignment.get("agent_role") or "").strip().lower()
        role_guidance = _ROLE_GUIDANCE.get(role, "Stay within the delegated specialist domain.")
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": f"{self.REASONING_POLICY}\n\n{role_guidance}",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "assignment": assignment,
                        "collected_evidence": [item.to_dict() for item in projected],
                        "previous_decision_summaries": [
                            {
                                "action": item.action,
                                "decision_summary": item.decision_summary,
                                "current_hypothesis": item.current_hypothesis,
                                "evidence_needed": item.evidence_needed,
                            }
                            for item in decisions[-4:]
                        ],
                        "instruction": (
                            "Describe WHAT evidence is needed. Do not mention MCP tool names; "
                            "Qwen owns action/tool selection."
                        ),
                    },
                    default=str,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._invoke_with_provider_slot(
                    self.reasoning_provider,
                    self._native_reasoning_request(messages),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The previous structured reasoning response was invalid. Return "
                                "only one schema-valid operational decision. If action is "
                                "gather_evidence, evidence_needed must be non-empty. Validation "
                                f"feedback: {exc}"
                            ),
                        }
                    )
        raise ReActInvestigationError(
            f"Gemma native structured reasoning failed: {last_error}"
        ) from last_error

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
