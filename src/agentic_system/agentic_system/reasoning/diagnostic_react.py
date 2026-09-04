from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import httpx
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from spade_llm.context import ContextManager

from .langchain_agent import (
    AssistanceDomain,
    DiagnosisStatus,
    ReActEvidence,
    ReActInvestigationError,
    SpecialistReActExecutor as _BaseSpecialistReActExecutor,
)


_ROLE_GUIDANCE = {
    "system_engineer": (
        "You are the System Engineer. Prioritize host/container resource saturation, processes, "
        "threads, memory, disk, runtime state and operating-system evidence. Cross-domain evidence "
        "may support the hypothesis, but first exhaust safe system diagnostics that can discriminate "
        "the reported resource anomaly."
    ),
    "network_engineer": (
        "You are the Network Engineer. Prioritize network paths, sockets, connection state, DNS, "
        "TCP reachability, service latency and network telemetry. Use application or system evidence "
        "only when it helps distinguish a network-path cause."
    ),
    "application_engineer": (
        "You are the Application Engineer. Prioritize application logs, API behaviour, service "
        "dependencies, HTTP checks and project-specific architecture. Use lower-level evidence when "
        "it helps explain an application symptom."
    ),
    "software_developer": (
        "You are the Software Developer. Prioritize code/runtime behaviour, implementation-specific "
        "failure modes, process behaviour, logs and documented service semantics."
    ),
}

_ROLE_DOMAIN = {
    "system_engineer": "system",
    "network_engineer": "network",
    "application_engineer": "application",
    "software_developer": "software",
}


class _DiagnosticFinalOutput(BaseModel):
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
    def _validate_closure(self) -> "_DiagnosticFinalOutput":
        if self.diagnosis_status in {"confirmed", "probable"}:
            if not self.root_cause:
                raise ValueError(f"{self.diagnosis_status} diagnosis requires a root_cause")
            if not self.causal_chain:
                raise ValueError(f"{self.diagnosis_status} diagnosis requires a causal_chain")

        if self.diagnosis_status == "confirmed":
            if self.assistance_required or self.assistance_domain is not None:
                raise ValueError("confirmed diagnosis cannot request diagnostic peer assistance")
            return self

        if self.assistance_required and self.assistance_domain is None:
            raise ValueError("assistance_required=true requires assistance_domain")
        if not self.assistance_required and self.assistance_domain is not None:
            raise ValueError("assistance_domain must be null when assistance_required=false")
        return self


class _GemmaDiagnosticFinalizer:
    def __init__(self, *, model: str, base_url: str, timeout_seconds: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.schema = _DiagnosticFinalOutput.model_json_schema()

    async def ainvoke(self, messages: list[dict[str, Any]]) -> _DiagnosticFinalOutput:
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
                    "Return only an object conforming to this JSON Schema: "
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
        return _DiagnosticFinalOutput.model_validate_json(content)


class SpecialistReActExecutor(_BaseSpecialistReActExecutor):
    """Evidence-first extension of the tested Gemma -> Qwen -> MCP specialist loop."""

    REASONING_POLICY = """
You are the reasoning component of an IT monitoring specialist agent.
AgentSpeak has already committed the investigation intention. You do NOT call tools and you do
NOT choose tool names. Return only a concise auditable operational decision, never private
chain-of-thought.

Safe diagnostic evidence that the action layer can collect includes: current resource state,
process and thread state, process hierarchy and /proc metadata, disk state, sockets, DNS/TCP/HTTP
connectivity between monitored services, OpenSearch metrics/logs, and project knowledge via RAG.

Choose exactly one action:
- gather_evidence: whenever another safe live observation can materially confirm/reject the
  current hypothesis or distinguish between plausible causes.
- finish: only when at least one live observation exists AND either (a) the evidence is sufficient
  for an evidence-backed diagnosis, or (b) no available safe diagnostic evidence can materially
  reduce the remaining uncertainty and cross-domain/human review is genuinely required.

Do not finish by merely recommending a diagnostic check that the action layer can perform now.
Do not invent evidence. Do not perform remediation. Prefer a small number of discriminating checks
over repeated equivalent checks. Request project/RAG knowledge when architecture, dependencies,
runbooks or service semantics are needed to interpret live telemetry.
""".strip()

    FINALIZATION_POLICY = """
Convert the completed investigation into the required diagnostic schema using only supplied
assignment, operational reasoning summaries and collected tool evidence.

Rules:
- confirmed: root_cause and causal_chain required; no peer assistance.
- probable: root_cause and causal_chain required. Peer assistance is optional and must be requested
  only when a DIFFERENT specialist domain can collect material evidence not already available.
- inconclusive: root_cause may be null. Peer assistance is optional. assistance_required=false is
  valid when the safe autonomous evidence budget is exhausted or no other specialist is needed.
- never request assistance from the same domain as the current specialist.
- findings must be directly supported by collected evidence.
- hypotheses are unresolved possibilities, not facts.
- recommended_next_steps may contain only checks that cannot already be executed through the
  currently available safe diagnostic action layer; never use it to postpone an available check.
- do not invent remediation. Remediation is synthesized later by the Technical Lead.
""".strip()

    def _build_finalizer(self) -> _GemmaDiagnosticFinalizer:
        return _GemmaDiagnosticFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
        )

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> Any:
        role = str(assignment.get("agent_role") or "").strip().lower()
        role_guidance = _ROLE_GUIDANCE.get(role, "Stay within the delegated specialist domain.")
        context = ContextManager(system_prompt=f"{self.REASONING_POLICY}\n\n{role_guidance}")
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
            "specialist-reasoning-step",
        )

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.reasoning_provider.get_llm_response(
                    context,
                    tools=None,
                    conversation_id="specialist-reasoning-step",
                    output_schema=self._reasoning_schema(),
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
                                "Return only the structured operational decision. State the "
                                "evidence need without naming a tool."
                            ),
                        },
                        "specialist-reasoning-step",
                    )
        raise ReActInvestigationError(f"Gemma reasoning step failed: {last_error}") from last_error

    @staticmethod
    def _reasoning_schema():
        # Keep the exact schema used by the parent executor without exporting a
        # second public contract.
        from .langchain_agent import _ReasoningDecision

        return _ReasoningDecision

    async def _select_tool(
        self,
        *,
        assignment: dict[str, Any],
        evidence_needed: str,
        evidence: list[ReActEvidence],
    ) -> tuple[str, dict[str, Any]]:
        previous_calls = [
            {"tool": item.tool, "arguments": item.arguments, "success": item.success}
            for item in evidence[-6:]
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
        for attempt in range(3):
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
                call = calls[0]
                name = str(call.get("name") or "").strip()
                if name not in self._tool_names:
                    raise RuntimeError(f"Qwen selected unavailable tool: {name!r}")
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    raise RuntimeError("Qwen tool arguments are not a JSON object")

                tool = self._langchain_tool_by_name[name]
                schema = tool.get_input_schema()
                validated = schema.model_validate(args)
                clean_args = validated.model_dump(exclude_none=True)

                duplicate = any(
                    item.success and item.tool == name and item.arguments == clean_args
                    for item in evidence
                )
                if duplicate:
                    raise RuntimeError(
                        "The same successful diagnostic call was already executed; select a "
                        "different action that adds discriminating evidence."
                    )
                return name, clean_args
            except asyncio.CancelledError:
                raise
            except (ValidationError, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The proposed tool call was rejected before execution. Repair it "
                                "without changing the evidence goal. Validation feedback: "
                                f"{exc}. Select exactly one bound tool with schema-valid arguments."
                            ),
                        }
                    )
        raise ReActInvestigationError(f"Qwen tool selection failed: {last_error}") from last_error

    async def _finalize(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> _DiagnosticFinalOutput:
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
                output = (
                    structured
                    if isinstance(structured, _DiagnosticFinalOutput)
                    else _DiagnosticFinalOutput.model_validate(structured)
                )
                if (
                    output.assistance_required
                    and current_domain
                    and output.assistance_domain == current_domain
                ):
                    raise ValueError(
                        f"{assignment.get('agent_role')} cannot request assistance from its own "
                        f"domain {current_domain!r}; request a different domain or set "
                        "assistance_required=false"
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
            f"Gemma structured diagnostic finalization failed: {validation_feedback or last_error}"
        ) from last_error
