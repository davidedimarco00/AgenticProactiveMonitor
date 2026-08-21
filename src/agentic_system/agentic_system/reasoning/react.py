from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any

from spade_llm.context import ContextManager, create_assistant_tool_call_message
from spade_llm.providers.base_provider import BaseLLMProvider
from spade_llm.tools import LLMTool


LOGGER = logging.getLogger("agentic_system.reasoning.react")
_DEFAULT_MAX_OBSERVATION_CHARS = 6000
_DEFAULT_EVIDENCE_EXCERPT_CHARS = 1200
_DEFAULT_FINALIZATION_MAX_ATTEMPTS = 3
_ALLOWED_ASSISTANCE_DOMAINS = {"system", "network", "application", "software"}


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
    """Execute one specialist investigation as a bounded ReAct loop.

    The executor deliberately reuses SPADE-LLM primitives instead of introducing
    another tool framework: the agent's ContextManager, provider and discovered
    LLMTool instances are used directly. The project owns only the durable-task
    boundary, the maximum number of operational steps and the structured result
    contract needed by the Technical Lead.
    """

    FINALIZATION_INSTRUCTION = """
Stop requesting tools. Produce the investigation result as ONE JSON object and no
other text. Base it only on the evidence already observed in this conversation.
Do not invent metrics, logs, commands, tool results or root causes.

Required JSON fields:
- summary: short technical summary
- confidence: number from 0 to 1
- findings: array of concise evidence-backed findings
- hypotheses: array of plausible hypotheses, clearly separated from findings
- recommended_next_steps: array of diagnostic next steps; do not perform remediation
- assistance_required: boolean
- assistance_domain: one of system, network, application, software, or null

If evidence is insufficient, say so explicitly, lower confidence, and request the
most appropriate assistance domain when useful.
""".strip()

    def __init__(
        self,
        *,
        provider: BaseLLMProvider,
        context: ContextManager,
        tools: list[LLMTool],
        max_steps: int = 6,
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

        # Interaction-memory helpers are not live diagnostic evidence. The
        # specialist keeps its normal SPADE-LLM memory, while the operational
        # ReAct loop exposes MCP/RAG tools only.
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
                break

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
            response = await self.provider.get_llm_response(
                self.context,
                tools=None,
                conversation_id=conversation_id,
            )
            text = str(response.get("text") or "").strip()
            try:
                if not text:
                    raise ReActInvestigationError(
                        "Reasoning model returned an empty final result"
                    )
                payload = self._parse_final_payload(text)
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
                            "all required fields."
                        ),
                    },
                    conversation_id,
                )

        raise ReActInvestigationError(
            "Specialist ReAct finalization failed after "
            f"{self.finalization_max_attempts} attempt(s): {last_error}"
        )

    @classmethod
    def _parse_final_payload(cls, text: str) -> dict[str, Any]:
        candidate = text.strip()
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
        if assistance_required and assistance_domain is None:
            raise ReActInvestigationError(
                "assistance_domain is required when assistance_required is true"
            )

        return {
            "summary": summary,
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
            "Your BDI layer has committed the intention `investigate_incident`. "
            "Execute that intention operationally with ReAct: reason about what evidence "
            "is missing, call an appropriate MCP or RAG tool, observe the result, then "
            "reason again. Continue only while another observation can materially improve "
            "the investigation. Use `search_knowledge` when project architecture, runbooks "
            "or known system-specific facts are relevant. Do not invent observations and "
            "do not perform remediation. At least one live evidence tool must be attempted "
            "before final conclusions. When evidence is sufficient, stop calling tools; a "
            "separate finalization prompt will request the structured result.\n\n"
            "Investigation assignment:\n"
            + json.dumps(assignment, separators=(",", ":"), sort_keys=True)
        )