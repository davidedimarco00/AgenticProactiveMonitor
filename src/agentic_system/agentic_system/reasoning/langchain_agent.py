from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any, Literal

import httpx
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
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
    """Small native Ollama adapter used only to serialize completed ReAct evidence.

    The operational agent loop remains LangChain-based. Finalization deliberately
    uses Ollama's `/api/chat` JSON-schema contract directly so a thinking model
    cannot hide the final JSON in a framework-specific output-parser path.
    """

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
    """Execute a BDI intention with LangChain ReAct and native Ollama finalization.

    SPADE-LLM owns identity, XMPP, MCP discovery and persistent interaction memory.
    AgentSpeak commits the intention. LangChain owns the operational
    Reason -> Act -> Observe loop. After tool use, the same Qwen model uses
    Ollama's native JSON-schema output to encode the evidence-backed conclusion.
    """

    SYSTEM_POLICY = """
Your AgentSpeak BDI layer has already committed the investigation intention.
Execute only that intention through evidence-driven ReAct.

Maintain a leading causal hypothesis, identify evidence that can confirm or reject it,
call the available MCP/RAG tools, observe their results, and update the hypothesis.
You MUST attempt at least one operational tool before concluding. Do not stop while an
available tool in your own domain can materially test the leading hypothesis. Use the
knowledge-base tool when project architecture, runbooks, or system-specific facts matter.

Stop gathering evidence only when either:
1. the root cause and causal chain are supported strongly enough for a confirmed diagnosis; or
2. the remaining uncertainty requires another specialist domain.

Never invent observations. Separate findings from hypotheses. Do not perform remediation.
A separate schema-finalization step will encode your conclusion after ReAct completes.
""".strip()

    FINALIZATION_POLICY = """
Convert the completed investigation into the required diagnostic schema.

Use only the supplied assignment, tool evidence, and working conclusion. Never invent
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
        max_steps: int = 10,
        tool_timeout_seconds: float = 30.0,
        max_observation_chars: int = _DEFAULT_MAX_OBSERVATION_CHARS,
        agent: Any | None = None,
        finalizer: Any | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than zero")
        if max_observation_chars <= 0:
            raise ValueError("max_observation_chars must be greater than zero")

        self.provider = provider
        self.context = context
        self.tools = [tool for tool in tools if tool.name != "remember_interaction_info"]
        if not self.tools:
            raise ValueError("Specialist ReAct requires at least one operational tool")

        self.max_steps = max_steps
        self.tool_timeout_seconds = tool_timeout_seconds
        self.max_observation_chars = max_observation_chars
        self._tool_names = {tool.name for tool in self.tools}
        self._langchain_tools = [self._adapt_tool(tool) for tool in self.tools]

        model = self._build_model() if agent is None else None
        self._agent = agent or self._build_agent(model)
        self._finalizer = finalizer or self._build_finalizer()

    def _build_model(self) -> ChatOllama:
        model_name = self._ollama_model_name()
        base_url = self._ollama_base_url()
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=0,
        )

    def _build_finalizer(self) -> _OllamaSchemaFinalizer:
        return _OllamaSchemaFinalizer(
            model=self._ollama_model_name(),
            base_url=self._ollama_base_url(),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
        )

    def _build_agent(self, model: Any) -> Any:
        role_prompt = self._system_prompt_from_spade_context()
        system_prompt = f"{role_prompt}\n\n{self.SYSTEM_POLICY}".strip()
        return create_agent(
            model=model,
            tools=self._langchain_tools,
            system_prompt=system_prompt,
        )

    def _ollama_model_name(self) -> str:
        raw = str(getattr(self.provider, "model", "")).strip()
        for prefix in ("ollama/", "ollama_native/"):
            if raw.startswith(prefix):
                return raw[len(prefix) :]
        if raw:
            return raw
        raise ValueError("Specialist provider does not expose a model name")

    def _ollama_base_url(self) -> str:
        base_url = str(getattr(self.provider, "base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Specialist provider does not expose an Ollama base_url")
        return base_url

    def _system_prompt_from_spade_context(self) -> str:
        try:
            prompt = self.context.get_prompt(None)
        except Exception:
            return "You are an IT monitoring specialist agent."
        for message in prompt:
            if isinstance(message, dict) and message.get("role") == "system":
                content = str(message.get("content") or "").strip()
                if content:
                    return content
        return "You are an IT monitoring specialist agent."

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

        conversation_id = f"react:{agent_role}:{incident_id}:{task_id}"
        investigation_prompt = self._investigation_prompt(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=agent_role,
            severity=severity,
            entity=entity,
            anomaly=anomaly,
        )
        self.context.add_message_dict(
            {"role": "user", "content": investigation_prompt},
            conversation_id,
        )

        state = await self._invoke_agent(
            {"messages": [{"role": "user", "content": investigation_prompt}]}
        )
        evidence, tools_used, react_steps = self._extract_execution(
            state.get("messages") or []
        )

        if not evidence:
            retry_messages = list(state.get("messages") or [])
            retry_messages.append(
                {
                    "role": "user",
                    "content": (
                        "No live operational evidence has been collected. Continue the same "
                        "investigation and call at least one suitable MCP or RAG tool before "
                        "concluding."
                    ),
                }
            )
            state = await self._invoke_agent({"messages": retry_messages})
            evidence, tools_used, react_steps = self._extract_execution(
                state.get("messages") or []
            )

        if not evidence:
            raise ReActInvestigationError(
                f"No operational tool was selected within {self.max_steps} ReAct steps"
            )
        if react_steps > self.max_steps:
            raise ReActInvestigationError(
                f"LangChain ReAct exceeded the configured {self.max_steps} model steps"
            )

        output = await self._finalize(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=agent_role,
            severity=severity,
            entity=entity,
            anomaly=anomaly,
            evidence=evidence,
            messages=state.get("messages") or [],
        )

        self._sync_spade_context(
            messages=state.get("messages") or [],
            conversation_id=conversation_id,
        )
        self.context.add_assistant_message(
            output.model_dump_json(),
            conversation_id,
        )

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
            react_steps=react_steps,
            tools_used=tuple(tools_used),
            conversation_id=conversation_id,
        )

    async def _invoke_agent(self, state: dict[str, Any]) -> dict[str, Any]:
        config = {"recursion_limit": max(10, self.max_steps * 2 + 4)}
        try:
            result = await self._invoke_with_inference_slot(
                self._agent.ainvoke(state, config=config)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ReActInvestigationError(f"LangChain ReAct execution failed: {exc}") from exc
        if not isinstance(result, dict):
            raise ReActInvestigationError("LangChain agent returned an invalid state")
        return result

    async def _finalize(
        self,
        *,
        task_id: str,
        incident_id: str,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
        evidence: list[ReActEvidence],
        messages: list[Any],
    ) -> _SpecialistFinalOutput:
        assignment = {
            "task_id": task_id,
            "incident_id": incident_id,
            "agent_role": agent_role,
            "severity": severity,
            "entity": entity,
            "anomaly": anomaly,
        }
        working_conclusion = self._last_ai_text(messages)
        prompt = (
            f"{self.FINALIZATION_POLICY}\n\n"
            "Assignment:\n"
            f"{json.dumps(assignment, default=str, ensure_ascii=False)}\n\n"
            "Collected tool evidence:\n"
            f"{json.dumps([item.to_dict() for item in evidence], default=str, ensure_ascii=False)}"
        )
        if working_conclusion:
            prompt += f"\n\nWorking conclusion from the ReAct loop:\n{working_conclusion}"

        last_error: Exception | None = None
        for attempt in range(2):
            final_prompt = prompt
            if attempt:
                final_prompt += (
                    "\n\nThe previous schema finalization failed validation. Re-evaluate the "
                    "same evidence and return a schema-valid diagnosis without adding facts."
                )
            try:
                structured = await self._invoke_with_inference_slot(
                    self._finalizer.ainvoke(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "You are the structured finalization stage of an IT "
                                    "diagnostic agent. Do not call tools."
                                ),
                            },
                            {"role": "user", "content": final_prompt},
                        ]
                    )
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
            raise ReActInvestigationError(
                self._validation_error_message(last_error)
            ) from last_error
        raise ReActInvestigationError(
            f"Ollama structured finalization failed: {last_error}"
        ) from last_error

    async def _invoke_with_inference_slot(self, awaitable: Any) -> Any:
        slot = getattr(self.provider, "inference_slot", None)
        if callable(slot):
            async with slot():
                return await awaitable
        return await awaitable

    def _extract_execution(
        self,
        messages: list[Any],
    ) -> tuple[list[ReActEvidence], list[str], int]:
        calls: dict[str, tuple[int, str, dict[str, Any]]] = {}
        evidence: list[ReActEvidence] = []
        tools_used: list[str] = []
        step = 0

        for message in messages:
            if isinstance(message, AIMessage):
                step += 1
                for raw_call in message.tool_calls or []:
                    name = str(raw_call.get("name") or "").strip()
                    if name not in self._tool_names:
                        continue
                    call_id = str(raw_call.get("id") or "").strip()
                    args = raw_call.get("args") or {}
                    if not isinstance(args, dict):
                        args = {}
                    calls[call_id] = (step, name, dict(args))
                    if name not in tools_used:
                        tools_used.append(name)
                continue

            if not isinstance(message, ToolMessage):
                continue
            call_id = str(message.tool_call_id or "").strip()
            metadata = calls.get(call_id)
            if metadata is None:
                continue
            call_step, name, args = metadata
            wrapper = self._decode_tool_message(message.content)
            success = bool(wrapper.get("success", False))
            observation = (
                wrapper.get("observation")
                if success
                else {"error": str(wrapper.get("error") or "Tool execution failed")}
            )
            evidence.append(
                ReActEvidence(
                    step=call_step,
                    tool=name,
                    arguments=args,
                    observation=self._evidence_excerpt(observation),
                    success=success,
                )
            )

        return evidence, tools_used, max(step, 1)

    @staticmethod
    def _decode_tool_message(content: Any) -> dict[str, Any]:
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

    def _sync_spade_context(self, *, messages: list[Any], conversation_id: str) -> None:
        calls: dict[str, str] = {}
        for message in messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                converted: list[dict[str, Any]] = []
                for raw_call in message.tool_calls:
                    name = str(raw_call.get("name") or "").strip()
                    if name not in self._tool_names:
                        continue
                    call_id = str(raw_call.get("id") or "").strip()
                    args = raw_call.get("args") or {}
                    if not isinstance(args, dict):
                        args = {}
                    calls[call_id] = name
                    converted.append(
                        {"id": call_id, "name": name, "arguments": dict(args)}
                    )
                if converted:
                    self.context.add_message_dict(
                        create_assistant_tool_call_message(converted),
                        conversation_id,
                    )
                continue

            if isinstance(message, ToolMessage):
                call_id = str(message.tool_call_id or "").strip()
                name = calls.get(call_id)
                if name is None:
                    continue
                wrapper = self._decode_tool_message(message.content)
                result = wrapper.get("observation") if wrapper.get("success") else wrapper
                self.context.add_tool_result(name, result, call_id, conversation_id)

    @staticmethod
    def _last_ai_text(messages: list[Any]) -> str:
        for message in reversed(messages):
            if not isinstance(message, AIMessage):
                continue
            content = message.content
            if isinstance(content, str):
                return content.strip()
            if content:
                return json.dumps(content, default=str, ensure_ascii=False)
        return ""

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
            "Investigate the following BDI-committed incident task. Use live MCP/RAG "
            "evidence to test causal hypotheses. The final schema will be generated after "
            "the ReAct loop from the evidence you collect.\n\n"
            "Investigation assignment:\n"
            + json.dumps(assignment, separators=(",", ":"), sort_keys=True)
        )
