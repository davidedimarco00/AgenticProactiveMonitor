from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from spade_llm.context import ContextManager, create_assistant_tool_call_message
from spade_llm.providers.base_provider import BaseLLMProvider
from spade_llm.tools import LLMTool


LOGGER = logging.getLogger("agentic_system.reasoning.react")
_DEFAULT_MAX_OBSERVATION_CHARS = 6000
_DEFAULT_EVIDENCE_EXCERPT_CHARS = 1200
_DEFAULT_FINALIZATION_MAX_ATTEMPTS = 3
_ALLOWED_ASSISTANCE_DOMAINS = {"system", "network", "application", "software"}
_ALLOWED_DIAGNOSIS_STATUSES = {"confirmed", "probable", "inconclusive"}

DiagnosisStatus = Literal["confirmed", "probable", "inconclusive"]
AssistanceDomain = Literal["system", "network", "application", "software"]


class _SpecialistFinalOutput(BaseModel):
    summary: str
    diagnosis_status: DiagnosisStatus
    root_cause: str | None = None
    causal_chain: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[str]
    hypotheses: list[str]
    recommended_next_steps: list[str]
    assistance_required: bool
    assistance_domain: AssistanceDomain | None = None


class ReActInvestigationError(RuntimeError):
    """Raised when a specialist cannot complete a bounded ReAct investigation."""


@dataclass(frozen=True, slots=True)
class ReActEvidence:
    step: int
    tool: str
    arguments: dict[str, Any]
    observation: Any
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "observation": self.observation,
            "success": self.success,
        }


@dataclass(frozen=True, slots=True)
class ReActInvestigationResult:
    task_id: str
    incident_id: str
    agent_role: str
    summary: str
    diagnosis_status: str
    root_cause: str | None
    causal_chain: tuple[str, ...]
    confidence: float
    findings: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    hypotheses: tuple[str, ...]
    recommended_next_steps: tuple[str, ...]
    assistance_required: bool
    assistance_domain: str | None
    react_steps: int
    tools_used: tuple[str, ...]
    conversation_id: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "incident_id": self.incident_id,
            "agent_role": self.agent_role,
            "status": "completed",
            "summary": self.summary,
            "diagnosis_status": self.diagnosis_status,
            "root_cause": self.root_cause,
            "causal_chain": list(self.causal_chain),
            "confidence": self.confidence,
            "findings": list(self.findings),
            "evidence": [dict(item) for item in self.evidence],
            "hypotheses": list(self.hypotheses),
            "recommended_next_steps": list(self.recommended_next_steps),
            "assistance_required": self.assistance_required,
            "assistance_domain": self.assistance_domain,
            "react_steps": self.react_steps,
            "tools_used": list(self.tools_used),
            "conversation_id": self.conversation_id,
        }


class SpecialistReActExecutor:
    """Execute one specialist investigation as a bounded diagnostic ReAct loop."""

    DIAGNOSTIC_READINESS_INSTRUCTION = """
Before ending the investigation, perform a diagnostic closure check.

Do NOT stop merely because you collected some evidence. Ask whether the observed anomaly
has a causal explanation. If a diagnostic action in your own domain can materially test
the leading hypothesis, call the appropriate MCP/RAG tool now. In particular, execute
log, metric, runtime, connection or knowledge-base checks yourself instead of listing them
as future recommendations when the required tool is available.

You may stop requesting tools only when one of these is true:
1. the root cause is supported strongly enough to mark the diagnosis `confirmed`; or
2. the remaining uncertainty requires evidence from another specialist domain, in which
   case the final result must be `probable` or `inconclusive` and request assistance.

If you still need evidence that you can collect with the available tools, continue ReAct.
""".strip()

    FINALIZATION_INSTRUCTION = """
Stop requesting tools. Produce the investigation result as ONE JSON object and no other
text. Base it only on the evidence already observed in this conversation. Do not invent
metrics, logs, commands, tool results or root causes.

Required JSON fields:
- summary: short technical summary
- diagnosis_status: exactly confirmed, probable, or inconclusive
- root_cause: concise evidence-backed causal explanation, or null if inconclusive
- causal_chain: array connecting cause -> mechanism -> observed anomaly; use only observed
  or explicitly supported facts
- confidence: number from 0 to 1
- findings: array of concise evidence-backed findings
- hypotheses: array of plausible hypotheses, clearly separated from findings
- recommended_next_steps: only remaining diagnostic verification steps; do not perform
  remediation and do not list checks that you could already have executed with available tools
- assistance_required: boolean
- assistance_domain: one of system, network, application, software, or null

Use `confirmed` only when the observed evidence supports a root cause and causal chain.
A confirmed diagnosis must include a non-empty root_cause and causal_chain and must not
request another specialist. If the root cause is only probable or still inconclusive,
do not pretend the task is diagnostically complete: set assistance_required=true and
select the most appropriate other specialist domain. The Technical Lead will coordinate
that peer collaboration.
""".strip()

    def __init__(
        self,
        *,
        provider: BaseLLMProvider,
        context: ContextManager,
        tools: list[LLMTool],
        max_steps: int = 10,
        tool_timeout_seconds: float = 30.0,
        max_observation_chars: int = _DEFAULT_MAX_OBSERVATION_CHARS,
        finalization_max_attempts: int = _DEFAULT_FINALIZATION_MAX_ATTEMPTS,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than zero")
        if max_observation_chars <= 0:
            raise ValueError("max_observation_chars must be greater than zero")
        if finalization_max_attempts <= 0:
            raise ValueError("finalization_max_attempts must be greater than zero")

        operational_tools = [
            tool for tool in tools if tool.name != "remember_interaction_info"
        ]
        if not operational_tools:
            raise ValueError("Specialist ReAct requires at least one operational tool")

        self.provider = provider
        self.context = context
        self.tools = operational_tools
        self.max_steps = max_steps
        self.tool_timeout_seconds = tool_timeout_seconds
        self.max_observation_chars = max_observation_chars
        self.finalization_max_attempts = finalization_max_attempts

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
        task_id = task_id.strip()
        incident_id = incident_id.strip()
        agent_role = agent_role.strip().lower()
        if not task_id or not incident_id or not agent_role:
            raise ValueError("ReAct investigation requires task, incident and agent identity")

        conversation_id = f"react:{agent_role}:{incident_id}:{task_id}"
        self.context.add_message_dict(
            {
                "role": "user",
                "content": self._investigation_prompt(
                    task_id=task_id,
                    incident_id=incident_id,
                    agent_role=agent_role,
                    severity=severity,
                    entity=entity,
                    anomaly=anomaly,
                ),
            },
            conversation_id,
        )

        evidence: list[ReActEvidence] = []
        tools_used: list[str] = []
        tool_attempts = 0
        steps_executed = 0
        diagnostic_stop_challenged = False

        for step in range(1, self.max_steps + 1):
            steps_executed = step
            response = await self.provider.get_llm_response(
                self.context,
                tools=self.tools,
                conversation_id=conversation_id,
            )
            tool_calls = response.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                raise ReActInvestigationError("Provider returned invalid tool_calls")

            if not tool_calls:
                if tool_attempts == 0:
                    self.context.add_message_dict(
                        {
                            "role": "user",
                            "content": (
                                "No live evidence has been collected yet. Continue the ReAct "
                                "investigation and call at least one suitable MCP or RAG tool "
                                "before producing conclusions."
                            ),
                        },
                        conversation_id,
                    )
                    continue

                if not diagnostic_stop_challenged and step < self.max_steps:
                    diagnostic_stop_challenged = True
                    self.context.add_message_dict(
                        {
                            "role": "user",
                            "content": self.DIAGNOSTIC_READINESS_INSTRUCTION,
                        },
                        conversation_id,
                    )
                    continue
                break

            diagnostic_stop_challenged = False
            self.context.add_message_dict(
                create_assistant_tool_call_message(tool_calls),
                conversation_id,
            )

            for call in tool_calls:
                if not isinstance(call, dict):
                    raise ReActInvestigationError("Provider returned an invalid tool call")
                tool_attempts += 1
                tool_name = str(call.get("name") or "").strip()
                arguments = call.get("arguments") or {}
                tool_id = str(call.get("id") or f"react-{step}-{tool_attempts}")
                if not isinstance(arguments, dict):
                    arguments = {}

                tool = next((candidate for candidate in self.tools if candidate.name == tool_name), None)
                if tool is None:
                    observation = {"error": f"Tool {tool_name!r} is not available"}
                    self.context.add_tool_result(
                        tool_name or "unknown_tool",
                        observation,
                        tool_id,
                        conversation_id,
                    )
                    evidence.append(
                        ReActEvidence(
                            step=step,
                            tool=tool_name or "unknown_tool",
                            arguments=dict(arguments),
                            observation=observation,
                            success=False,
                        )
                    )
                    continue

                if tool.name not in tools_used:
                    tools_used.append(tool.name)

                try:
                    raw_result = await asyncio.wait_for(
                        tool.execute(**arguments),
                        timeout=self.tool_timeout_seconds,
                    )
                    observation = self._normalize_observation(raw_result)
                    success = True
                except asyncio.TimeoutError:
                    observation = {
                        "error": (
                            f"Tool {tool.name} exceeded timeout "
                            f"{self.tool_timeout_seconds:.1f}s"
                        )
                    }
                    success = False
                except Exception as exc:
                    observation = {"error": f"{type(exc).__name__}: {exc}"}
                    success = False

                self.context.add_tool_result(
                    tool.name,
                    observation,
                    tool_id,
                    conversation_id,
                )
                evidence.append(
                    ReActEvidence(
                        step=step,
                        tool=tool.name,
                        arguments=dict(arguments),
                        observation=self._evidence_excerpt(observation),
                        success=success,
                    )
                )

        if tool_attempts == 0:
            raise ReActInvestigationError(
                f"No operational tool was selected within {self.max_steps} ReAct steps"
            )

        self.context.add_message_dict(
            {"role": "user", "content": self.FINALIZATION_INSTRUCTION},
            conversation_id,
        )
        payload = await self._finalize_payload(conversation_id)
        self.context.add_assistant_message(
            json.dumps(payload, separators=(",", ":")),
            conversation_id,
        )

        return ReActInvestigationResult(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=agent_role,
            summary=str(payload["summary"]),
            diagnosis_status=str(payload["diagnosis_status"]),
            root_cause=payload["root_cause"],
            causal_chain=tuple(payload["causal_chain"]),
            confidence=float(payload["confidence"]),
            findings=tuple(payload["findings"]),
            evidence=tuple(item.to_dict() for item in evidence),
            hypotheses=tuple(payload["hypotheses"]),
            recommended_next_steps=tuple(payload["recommended_next_steps"]),
            assistance_required=bool(payload["assistance_required"]),
            assistance_domain=payload["assistance_domain"],
            react_steps=steps_executed,
            tools_used=tuple(tools_used),
            conversation_id=conversation_id,
        )

    async def _finalize_payload(self, conversation_id: str) -> dict[str, Any]:
        """Retry only structured finalization, preserving already collected evidence."""

        last_error: ReActInvestigationError | None = None
        for attempt in range(1, self.finalization_max_attempts + 1):
            try:
                response = await self.provider.get_llm_response(
                    self.context,
                    tools=None,
                    conversation_id=conversation_id,
                    output_schema=_SpecialistFinalOutput,
                )
            except Exception as structured_error:
                LOGGER.warning(
                    "Specialist structured finalization transport failed; falling back to "
                    "plain JSON generation: conversation=%s attempt=%d/%d error=%s",
                    conversation_id,
                    attempt,
                    self.finalization_max_attempts,
                    structured_error,
                )
                response = await self.provider.get_llm_response(
                    self.context,
                    tools=None,
                    conversation_id=conversation_id,
                    output_schema=None,
                )

            try:
                payload = self._payload_from_response(response)
                if attempt > 1:
                    LOGGER.warning(
                        "Specialist ReAct finalization recovered without repeating tool work: "
                        "conversation=%s attempt=%d/%d",
                        conversation_id,
                        attempt,
                        self.finalization_max_attempts,
                    )
                return payload
            except ReActInvestigationError as exc:
                last_error = exc
                if attempt >= self.finalization_max_attempts:
                    break
                LOGGER.warning(
                    "Specialist ReAct finalization rejected; retrying without new tool calls: "
                    "conversation=%s attempt=%d/%d error=%s",
                    conversation_id,
                    attempt,
                    self.finalization_max_attempts,
                    exc,
                )
                self.context.add_message_dict(
                    {
                        "role": "user",
                        "content": (
                            "The previous finalization response was empty or invalid. "
                            f"Validation error: {exc}. Do NOT call any tools and do NOT "
                            "repeat the investigation. Use the evidence already present in "
                            "this conversation and return ONLY the required JSON object with "
                            "all required fields. A non-confirmed diagnosis MUST request an "
                            "assistance domain."
                        ),
                    },
                    conversation_id,
                )

        raise ReActInvestigationError(
            "Specialist ReAct finalization failed after "
            f"{self.finalization_max_attempts} attempt(s): {last_error}"
        )

    @classmethod
    def _payload_from_response(cls, response: dict[str, Any]) -> dict[str, Any]:
        structured = response.get("structured")
        if structured is not None:
            if isinstance(structured, BaseModel):
                raw = structured.model_dump()
            elif isinstance(structured, dict):
                raw = dict(structured)
            else:
                raise ReActInvestigationError(
                    "Structured specialist result returned an unsupported object"
                )
            return cls._parse_final_payload(raw)

        text = str(response.get("text") or "").strip()
        if not text:
            raise ReActInvestigationError("Reasoning model returned an empty final result")
        return cls._parse_final_payload(text)

    @classmethod
    def _parse_final_payload(cls, value: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(value, dict):
            raw = dict(value)
        else:
            candidate = value.strip()
            if candidate.startswith("```"):
                lines = candidate.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                candidate = "\n".join(lines).strip()

            first = candidate.find("{")
            last = candidate.rfind("}")
            if first >= 0 and last >= first:
                candidate = candidate[first : last + 1]

            try:
                raw = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise ReActInvestigationError("Final specialist result is not valid JSON") from exc
            if not isinstance(raw, dict):
                raise ReActInvestigationError("Final specialist result must be a JSON object")

        summary = str(raw.get("summary") or "").strip()
        if not summary:
            raise ReActInvestigationError("Final specialist summary cannot be empty")

        diagnosis_status = str(raw.get("diagnosis_status") or "").strip().lower()
        if diagnosis_status not in _ALLOWED_DIAGNOSIS_STATUSES:
            raise ReActInvestigationError(
                f"Unsupported diagnosis_status: {diagnosis_status!r}"
            )

        root_cause_raw = raw.get("root_cause")
        root_cause = str(root_cause_raw).strip() if root_cause_raw is not None else None
        if root_cause in {"", "none", "null", "unknown", "unconfirmed"}:
            root_cause = None

        causal_chain = cls._string_list(raw.get("causal_chain"), "causal_chain")

        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ReActInvestigationError("Final confidence is invalid") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ReActInvestigationError("Final confidence must be between 0 and 1")

        findings = cls._string_list(raw.get("findings"), "findings")
        hypotheses = cls._string_list(raw.get("hypotheses"), "hypotheses")
        next_steps = cls._string_list(
            raw.get("recommended_next_steps"),
            "recommended_next_steps",
        )
        assistance_required = raw.get("assistance_required")
        if not isinstance(assistance_required, bool):
            raise ReActInvestigationError("assistance_required must be boolean")

        assistance_domain_raw = raw.get("assistance_domain")
        assistance_domain = (
            str(assistance_domain_raw).strip().lower()
            if assistance_domain_raw is not None
            else None
        )
        if assistance_domain in {"", "none", "null"}:
            assistance_domain = None
        if assistance_domain is not None and assistance_domain not in _ALLOWED_ASSISTANCE_DOMAINS:
            raise ReActInvestigationError(
                f"Unsupported assistance_domain: {assistance_domain!r}"
            )

        if diagnosis_status in {"confirmed", "probable"}:
            if not root_cause:
                raise ReActInvestigationError(
                    f"{diagnosis_status} diagnosis requires a root_cause"
                )
            if not causal_chain:
                raise ReActInvestigationError(
                    f"{diagnosis_status} diagnosis requires a causal_chain"
                )

        if diagnosis_status == "confirmed":
            if assistance_required or assistance_domain is not None:
                raise ReActInvestigationError(
                    "confirmed diagnosis cannot request diagnostic peer assistance"
                )
        else:
            if not assistance_required or assistance_domain is None:
                raise ReActInvestigationError(
                    "probable or inconclusive diagnosis must request an assistance domain"
                )

        return {
            "summary": summary,
            "diagnosis_status": diagnosis_status,
            "root_cause": root_cause,
            "causal_chain": causal_chain,
            "confidence": confidence,
            "findings": findings,
            "hypotheses": hypotheses,
            "recommended_next_steps": next_steps,
            "assistance_required": assistance_required,
            "assistance_domain": assistance_domain,
        }

    @staticmethod
    def _string_list(value: Any, field: str) -> list[str]:
        if not isinstance(value, list):
            raise ReActInvestigationError(f"{field} must be an array")
        return [str(item).strip() for item in value if str(item).strip()]

    def _normalize_observation(self, value: Any) -> Any:
        try:
            serialized = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            serialized = json.dumps(str(value), ensure_ascii=False)

        if len(serialized) <= self.max_observation_chars:
            try:
                return json.loads(serialized)
            except json.JSONDecodeError:
                return serialized

        return {
            "truncated": True,
            "original_chars": len(serialized),
            "content": serialized[: self.max_observation_chars],
        }

    @staticmethod
    def _evidence_excerpt(observation: Any) -> Any:
        serialized = json.dumps(observation, default=str, ensure_ascii=False)
        if len(serialized) <= _DEFAULT_EVIDENCE_EXCERPT_CHARS:
            return observation
        return {
            "truncated": True,
            "content": serialized[:_DEFAULT_EVIDENCE_EXCERPT_CHARS],
        }

    @staticmethod
    def _investigation_prompt(
        *,
        task_id: str,
        incident_id: str,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> str:
        assignment = {
            "task_id": task_id,
            "incident_id": incident_id,
            "agent_role": agent_role,
            "severity": severity,
            "entity": entity,
            "anomaly": anomaly,
        }
        return (
            "Your BDI layer has committed an investigation intention. Execute that intention "
            "operationally with ReAct: reason about the leading causal hypothesis, identify "
            "the evidence needed to test it, call an appropriate MCP or RAG tool, observe the "
            "result, and update or reject the hypothesis. Continue until you can explain the "
            "anomaly with an evidence-backed root cause or until the missing evidence belongs "
            "to another specialist domain. Use `search_knowledge` when project architecture, "
            "runbooks or known system-specific facts are relevant. Do not invent observations "
            "and do not perform remediation. At least one live evidence tool must be attempted. "
            "Do not defer an available diagnostic check to the operator or to a future step: "
            "execute it now when it can materially test the hypothesis. When you think the "
            "investigation can stop, a diagnostic closure challenge may ask you to verify that "
            "there is either a supported root cause or a justified peer-assistance need.\n\n"
            "Investigation assignment:\n"
            + json.dumps(assignment, separators=(",", ":"), sort_keys=True)
        )
