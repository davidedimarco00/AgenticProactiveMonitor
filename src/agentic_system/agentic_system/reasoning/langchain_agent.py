from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import logging
from typing import Any, Callable, Literal

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from spade_llm.context import ContextManager, create_assistant_tool_call_message
from spade_llm.providers.base_provider import BaseLLMProvider
from spade_llm.tools import LLMTool


LOGGER = logging.getLogger("agentic_system.reasoning.langchain_agent")
_DEFAULT_MAX_OBSERVATION_CHARS = 6000
_DEFAULT_EVIDENCE_EXCERPT_CHARS = 1200

DiagnosisStatus = Literal["confirmed", "probable", "inconclusive"]
AssistanceDomain = Literal["system", "network", "application", "software"]
ReasoningAction = Literal["gather_evidence", "finish"]
TraceSink = Callable[[dict[str, Any]], Any]


class _ReasoningDecision(BaseModel):
    """Concise, auditable Gemma decision for one ReAct reasoning step.

    `decision_summary` is deliberately an operational summary, not private
    chain-of-thought. It explains what evidence is needed or why the specialist
    believes evidence collection can stop.
    """

    action: ReasoningAction
    decision_summary: str
    current_hypothesis: str | None = None
    evidence_needed: str | None = None

    @field_validator("decision_summary")
    @classmethod
    def _decision_summary_not_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("decision_summary cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_evidence_request(self) -> "_ReasoningDecision":
        if self.action == "gather_evidence":
            if not str(self.evidence_needed or "").strip():
                raise ValueError("gather_evidence requires evidence_needed")
        else:
            self.evidence_needed = None
        return self


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
    def _clean_string_lists(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _validate_diagnostic_closure(self) -> "_SpecialistFinalOutput":
        if self.diagnosis_status in {"confirmed", "probable"}:
            if not self.root_cause:
                raise ValueError(f"{self.diagnosis_status} diagnosis requires a root_cause")
            if not self.causal_chain:
                raise ValueError(f"{self.diagnosis_status} diagnosis requires a causal_chain")

        if self.diagnosis_status == "confirmed":
            if self.assistance_required or self.assistance_domain is not None:
                raise ValueError("confirmed diagnosis cannot request diagnostic peer assistance")
        elif not self.assistance_required or self.assistance_domain is None:
            raise ValueError(
                "probable or inconclusive diagnosis must request an assistance domain"
            )
        return self


class _OllamaSchemaFinalizer:
    """Native Ollama JSON-schema finalizer used for the Gemma diagnosis stage."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.schema = _SpecialistFinalOutput.model_json_schema()

    async def ainvoke(self, messages: list[dict[str, Any]]) -> _SpecialistFinalOutput:
        normalized_messages: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "user").strip() or "user"
            content = str(message.get("content") or "").strip()
            normalized_messages.append({"role": role, "content": content})

        schema_text = json.dumps(self.schema, ensure_ascii=False, separators=(",", ":"))
        normalized_messages.insert(
            1 if normalized_messages else 0,
            {
                "role": "system",
                "content": (
                    "Return only an object that conforms to this JSON Schema. "
                    f"JSON Schema: {schema_text}"
                ),
            },
        )

        payload = {
            "model": self.model,
            "messages": normalized_messages,
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
            raise RuntimeError("Ollama schema finalizer returned no message object")
        content = str(message.get("content") or "").strip()
        if not content:
            thinking = str(message.get("thinking") or "").strip()
            detail = ""
            if thinking:
                detail = f"; unexpected thinking-only response ({len(thinking)} chars)"
            raise RuntimeError(f"Ollama schema finalizer returned empty content{detail}")
        return _SpecialistFinalOutput.model_validate_json(content)


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
    """Hybrid specialist loop: Gemma reasons, Qwen selects tools, MCP observes.

    AgentSpeak has already committed the investigation intention before this
    executor starts. The loop deliberately separates cognitive responsibilities:

        Gemma Reason -> Qwen Act/tool selection -> MCP Observe -> Gemma Reason

    Gemma also produces the final evidence-backed diagnostic schema. Qwen never
    decides the diagnosis; it only maps a requested piece of evidence to one
    available MCP/RAG tool and its arguments.
    """

    REASONING_POLICY = """
You are the reasoning component of an IT monitoring specialist agent.
AgentSpeak has already committed the investigation intention. You do NOT call tools.

At each step, inspect only the assignment and collected evidence. Return a concise,
auditable operational decision, not private chain-of-thought.

Choose exactly one action:
- gather_evidence: when another live observation from the specialist's own domain can
  materially confirm or reject the current causal hypothesis. Describe the evidence needed
  without naming a tool unless the evidence itself naturally requires a known source.
- finish: only after at least one live tool observation exists and either the evidence is
  sufficient for a diagnosis, or the remaining uncertainty clearly requires another domain.

Do not invent observations. Do not perform remediation. Prefer discriminating evidence over
repeating equivalent checks. Use project/RAG knowledge when architecture, dependencies,
runbooks or project-specific facts are needed to interpret telemetry.
""".strip()

    TOOL_SELECTION_POLICY = """
You are the action-selection component of an IT monitoring agent. You do NOT diagnose.
Given the evidence requested by the reasoning model, call exactly ONE bound tool that best
collects that evidence. Populate its arguments from the incident assignment and current
context. Do not answer in natural language and do not call more than one tool.
""".strip()

    FINALIZATION_POLICY = """
Convert the completed investigation into the required diagnostic schema.

Use only the supplied assignment, Gemma reasoning summaries, and tool evidence. Never invent
observations or claim that a tool returned data that is not present in the evidence.

Rules:
- confirmed: root_cause and causal_chain are required; assistance_required must be false
  and assistance_domain must be null.
- probable: root_cause and causal_chain are required; assistance_required must be true
  and assistance_domain must identify the specialist domain needed next.
- inconclusive: assistance_required must be true and assistance_domain must identify the
  specialist domain needed next. root_cause may be null.
- findings contain observations supported by evidence.
- hypotheses contain unresolved causal possibilities.
- recommended_next_steps contain diagnostic verification only, never remediation.
""".strip()

    def __init__(
        self,
        *,
        provider: BaseLLMProvider,
        context: ContextManager,
        tools: list[LLMTool],
        tool_provider: BaseLLMProvider | None = None,
        max_steps: int = 10,
        tool_timeout_seconds: float = 30.0,
        max_observation_chars: int = _DEFAULT_MAX_OBSERVATION_CHARS,
        tool_selector: Any | None = None,
        finalizer: Any | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than zero")
        if max_observation_chars <= 0:
            raise ValueError("max_observation_chars must be greater than zero")

        self.provider = provider
        self.reasoning_provider = provider
        self.tool_provider = tool_provider or provider
        self.context = context
        self.tools = [tool for tool in tools if tool.name != "remember_interaction_info"]
        if not self.tools:
            raise ValueError("Specialist ReAct requires at least one operational tool")

        self.max_steps = max_steps
        self.tool_timeout_seconds = tool_timeout_seconds
        self.max_observation_chars = max_observation_chars
        self.trace_sink = trace_sink
        self._tool_names = {tool.name for tool in self.tools}
        self._langchain_tools = [self._adapt_tool(tool) for tool in self.tools]
        self._langchain_tool_by_name = {tool.name: tool for tool in self._langchain_tools}
        self._tool_selector = tool_selector or self._build_tool_selector()
        self._finalizer = finalizer or self._build_finalizer()

    def _build_tool_selector(self) -> Any:
        model = ChatOllama(
            model=self._ollama_model_name(self.tool_provider),
            base_url=self._ollama_base_url(self.tool_provider),
            temperature=0,
        )
        return model.bind_tools(self._langchain_tools)

    def _build_finalizer(self) -> _OllamaSchemaFinalizer:
        return _OllamaSchemaFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
        )

    @staticmethod
    def _ollama_model_name(provider: Any) -> str:
        raw = str(getattr(provider, "model", "")).strip()
        for prefix in ("ollama/", "ollama_native/"):
            if raw.startswith(prefix):
                return raw[len(prefix) :]
        if raw:
            return raw
        raise ValueError("LLM provider does not expose a model name")

    @staticmethod
    def _ollama_base_url(provider: Any) -> str:
        base_url = str(getattr(provider, "base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("LLM provider does not expose an Ollama base_url")
        return base_url

    def _adapt_tool(self, tool: LLMTool) -> StructuredTool:
        async def execute_spade_tool(**kwargs: Any) -> str:
            try:
                raw = await asyncio.wait_for(
                    tool.execute(**kwargs),
                    timeout=self.tool_timeout_seconds,
                )
                payload = {
                    "success": True,
                    "observation": self._normalize_observation(raw),
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

        await self._emit_trace(
            action="react_started",
            reason="AgentSpeak committed the investigation intention; Gemma starts evidence planning.",
            incident_id=incident_id,
            task_id=task_id,
            outcome=f"reasoning={self._ollama_model_name(self.reasoning_provider)}; tool_selection={self._ollama_model_name(self.tool_provider)}",
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

        output = await self._finalize(
            assignment=assignment,
            evidence=evidence,
            decisions=decisions,
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

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[_ReasoningDecision],
    ) -> _ReasoningDecision:
        context = ContextManager(system_prompt=self.REASONING_POLICY)
        context.add_message_dict(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "assignment": assignment,
                        "collected_evidence": [item.to_dict() for item in evidence],
                        "previous_decision_summaries": [
                            {
                                "action": item.action,
                                "decision_summary": item.decision_summary,
                                "current_hypothesis": item.current_hypothesis,
                                "evidence_needed": item.evidence_needed,
                            }
                            for item in decisions[-4:]
                        ],
                        "available_tools": [
                            {"name": tool.name, "description": tool.description}
                            for tool in self.tools
                        ],
                    },
                    default=str,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
            "specialist-reasoning-step",
        )

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.reasoning_provider.get_llm_response(
                    context,
                    tools=None,
                    conversation_id="specialist-reasoning-step",
                    output_schema=_ReasoningDecision,
                )
                return self._reasoning_from_response(response)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    context.add_message_dict(
                        {
                            "role": "user",
                            "content": (
                                "Return only the structured operational decision. Do not provide "
                                "private chain-of-thought."
                            ),
                        },
                        "specialist-reasoning-step",
                    )
        raise ReActInvestigationError(f"Gemma reasoning step failed: {last_error}") from last_error

    @staticmethod
    def _reasoning_from_response(response: dict[str, Any]) -> _ReasoningDecision:
        structured = response.get("structured")
        if isinstance(structured, _ReasoningDecision):
            return structured
        if isinstance(structured, BaseModel):
            return _ReasoningDecision.model_validate(structured.model_dump())
        if isinstance(structured, dict):
            return _ReasoningDecision.model_validate(structured)

        text = str(response.get("text") or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return _ReasoningDecision.model_validate_json(text)
        except Exception as exc:
            raise RuntimeError("Gemma reasoning did not return a valid structured decision") from exc

    async def _select_tool(
        self,
        *,
        assignment: dict[str, Any],
        evidence_needed: str,
        evidence: list[ReActEvidence],
    ) -> tuple[str, dict[str, Any]]:
        previous_calls = [
            {"tool": item.tool, "arguments": item.arguments, "success": item.success}
            for item in evidence[-5:]
        ]
        messages = [
            {"role": "system", "content": self.TOOL_SELECTION_POLICY},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "assignment": assignment,
                        "evidence_requested_by_gemma": evidence_needed,
                        "previous_tool_calls": previous_calls,
                    },
                    default=str,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._invoke_with_provider_slot(
                    self.tool_provider,
                    self._tool_selector.ainvoke(messages),
                )
                if not isinstance(response, AIMessage):
                    raise RuntimeError("Qwen tool selector returned a non-AI message")
                calls = list(response.tool_calls or [])
                if not calls:
                    raise RuntimeError("Qwen tool selector did not return a tool call")
                raw_call = calls[0]
                name = str(raw_call.get("name") or "").strip()
                if name not in self._tool_names:
                    raise RuntimeError(f"Qwen selected unavailable tool: {name!r}")
                args = raw_call.get("args") or {}
                if not isinstance(args, dict):
                    raise RuntimeError("Qwen tool arguments are not a JSON object")
                return name, dict(args)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "A tool call is mandatory. Select exactly one bound tool for the "
                                "requested evidence and provide valid arguments."
                            ),
                        }
                    )
        raise ReActInvestigationError(f"Qwen tool selection failed: {last_error}") from last_error

    async def _execute_tool(
        self,
        *,
        step: int,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ReActEvidence:
        tool = self._langchain_tool_by_name[tool_name]
        raw = await tool.ainvoke(arguments)
        wrapper = self._decode_tool_result(raw)
        success = bool(wrapper.get("success", False))
        observation = (
            wrapper.get("observation")
            if success
            else {"error": str(wrapper.get("error") or "Tool execution failed")}
        )
        return ReActEvidence(
            step=step,
            tool=tool_name,
            arguments=dict(arguments),
            observation=self._evidence_excerpt(observation),
            success=success,
        )

    async def _finalize(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[_ReasoningDecision],
    ) -> _SpecialistFinalOutput:
        prompt = (
            f"{self.FINALIZATION_POLICY}\n\n"
            "Assignment:\n"
            f"{json.dumps(assignment, default=str, ensure_ascii=False)}\n\n"
            "Gemma operational reasoning summaries:\n"
            f"{json.dumps([item.model_dump() for item in decisions], default=str, ensure_ascii=False)}\n\n"
            "Collected tool evidence:\n"
            f"{json.dumps([item.to_dict() for item in evidence], default=str, ensure_ascii=False)}"
        )

        last_error: Exception | None = None
        for attempt in range(2):
            final_prompt = prompt
            if attempt:
                final_prompt += (
                    "\n\nThe previous schema finalization failed validation. Re-evaluate the "
                    "same evidence and return a schema-valid diagnosis without adding facts."
                )
            try:
                structured = await self._invoke_with_provider_slot(
                    self.reasoning_provider,
                    self._finalizer.ainvoke(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "You are Gemma, the diagnostic finalization stage of an IT "
                                    "specialist. Do not call tools."
                                ),
                            },
                            {"role": "user", "content": final_prompt},
                        ]
                    ),
                )
                return (
                    structured
                    if isinstance(structured, _SpecialistFinalOutput)
                    else _SpecialistFinalOutput.model_validate(structured)
                )
            except asyncio.CancelledError:
                raise
            except ValidationError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc

        if isinstance(last_error, ValidationError):
            raise ReActInvestigationError(self._validation_error_message(last_error)) from last_error
        raise ReActInvestigationError(
            f"Gemma structured finalization failed: {last_error}"
        ) from last_error

    async def _invoke_with_provider_slot(self, provider: Any, awaitable: Any) -> Any:
        slot = getattr(provider, "inference_slot", None)
        if callable(slot):
            async with slot():
                return await awaitable
        return await awaitable

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
        if self.trace_sink is None:
            return
        payload = {
            "action": action,
            "reason": reason,
            "incident_id": incident_id,
            "task_id": task_id,
            "tool": tool,
            "outcome": outcome,
            "details": dict(details or {}),
        }
        result = self.trace_sink(payload)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _decode_tool_result(content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return dict(content)
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"success": True, "observation": text}
        if isinstance(value, dict):
            return value
        return {"success": True, "observation": value}

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
    def _observation_summary(observation: Any, *, success: bool) -> str:
        if not success:
            if isinstance(observation, dict):
                return str(observation.get("error") or "Tool execution failed")[:700]
            return str(observation)[:700]
        serialized = json.dumps(observation, default=str, ensure_ascii=False)
        return serialized[:700]

    @staticmethod
    def _validation_error_message(exc: ValidationError) -> str:
        message = str(exc)
        for expected in (
            "probable or inconclusive diagnosis must request an assistance domain",
            "confirmed diagnosis cannot request diagnostic peer assistance",
            "confirmed diagnosis requires a root_cause",
            "confirmed diagnosis requires a causal_chain",
            "probable diagnosis requires a root_cause",
            "probable diagnosis requires a causal_chain",
        ):
            if expected in message:
                return expected
        return f"Structured specialist result is invalid: {exc}"
