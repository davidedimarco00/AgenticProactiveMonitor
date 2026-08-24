from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from spade_llm.context import ContextManager

from .diagnostic_react import _ROLE_DOMAIN
from .langchain_agent import (
    ReActEvidence,
    ReActInvestigationError,
    ReActInvestigationResult,
    _ReasoningDecision,
)
from .models import RoleLLMProvider
from .observation_aware_react import SpecialistReActExecutor as _CoreReActExecutor
from .react_contracts import (
    _ContextAwarePromptGemmaDiagnosticFinalizer,
    _EvidenceRequest,
    _PromptDiagnosticFinalOutput,
    _StructuredReasoningDecision,
    _configured_ollama_context,
)
from .react_policies import FINALIZATION_POLICY, REASONING_POLICY, TOOL_SELECTION_POLICY


class SpecialistReActExecutor(_CoreReActExecutor):
    """Canonical specialist ReAct executor.

    The previous implementation accumulated several feature subclasses for
    structured reasoning, prompt policy, RAG grounding, collaboration, Ollama
    context handling and incident focus. Those concerns are now consolidated in
    this one project-level executor while the lower-level core continues to own
    generic observation handling, schema validation and the bounded ReAct loop.

    Responsibilities remain separated:
    - Gemma decides WHAT evidence is needed and interprets observations;
    - Qwen selects HOW to collect one requested observation;
    - MCP/RAG provides bounded observations;
    - Python enforces schemas, context, limits, grounding and trace invariants.
    """

    REASONING_POLICY = REASONING_POLICY
    TOOL_SELECTION_POLICY = TOOL_SELECTION_POLICY
    FINALIZATION_POLICY = FINALIZATION_POLICY

    INITIAL_RAG_LIMIT = 4
    _STATIC_GROUNDING_KEY = "static_project_grounding"
    _HEALTH_ONLY_TOOL_NAMES = {"ping", "apm_mcp_ping"}
    _DUPLICATE_SELECTION_MARKER = "same successful diagnostic call was already executed"
    _EMPTY_CAUSE_MARKERS = {"", "none", "null", "unknown", "unconfirmed", "n/a"}

    # Semantic action families are tool capabilities, not scenario workflows.
    # Gemma chooses one evidence family; Qwen remains free to select one of the
    # compatible bound tools in that family.
    _EVIDENCE_KIND_TOOL_SUFFIXES: dict[str, tuple[str, ...]] = {
        "metric_history": ("get_metrics",),
        "runtime_resource_state": ("get_runtime_stats",),
        "process_attribution": ("get_processes",),
        "process_detail": ("inspect_process", "get_process_threads", "get_process_tree"),
        "log_evidence": ("get_logs", "search_logs"),
        "network_path": (
            "get_network_connections",
            "resolve_service_dns",
            "test_tcp_connection",
            "test_icmp_reachability",
        ),
        "application_endpoint": ("check_http_endpoint",),
        "storage_state": ("get_disk_usage",),
        "static_knowledge": ("search_knowledge",),
    }
    _RESOURCE_SIGNAL_FAMILIES = {"container_cpu", "container_memory"}
    _RESOURCE_CROSS_DOMAIN_KINDS = {"network_path", "application_endpoint"}
    _PRIMARY_TARGET_ARGUMENTS = (
        "host_id",
        "service",
        "service_name",
        "component",
        "container_name",
    )
    _RELATED_TARGET_ARGUMENTS = ("target_host", "target_service", "dependency")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        tools = kwargs.get("tools")
        if tools is not None:
            kwargs["tools"] = self._filter_specialist_tools(list(tools))
        super().__init__(*args, **kwargs)
        self._initial_rag_context_by_task: dict[tuple[str, str], dict[str, Any]] = {}
        self._bounded_evidence_snapshot: list[ReActEvidence] = []
        self._bounded_decisions_snapshot: list[Any] = []
        self._active_reasoning_assignment: dict[str, Any] | None = None
        self._active_selection_assignment: dict[str, Any] | None = None
        self._active_evidence_request: _EvidenceRequest | None = None

    # ------------------------------------------------------------------
    # Deterministic incident focus
    # ------------------------------------------------------------------
    @classmethod
    def _filter_specialist_tools(cls, tools: list[Any]) -> list[Any]:
        return [
            tool
            for tool in tools
            if str(getattr(tool, "name", "")).strip().lower()
            not in cls._HEALTH_ONLY_TOOL_NAMES
        ]

    @staticmethod
    def _detector_name(*, entity: str, anomaly: dict[str, Any]) -> str:
        return str(anomaly.get("detector_name") or entity or "unknown").strip()

    @classmethod
    def _signal_family(
        cls,
        *,
        entity: str,
        anomaly: dict[str, Any],
    ) -> tuple[str, str]:
        measurement = str(anomaly.get("measurement_name") or "").strip().lower()
        feature_field = str(anomaly.get("feature_field") or "").strip().lower()
        combined = f"{measurement} {feature_field}"

        if "docker_container_cpu" in combined:
            return "container_cpu", "detector_metadata"
        if "docker_container_mem" in combined:
            return "container_memory", "detector_metadata"
        if "network_transport_latency" in combined:
            return "network_transport_latency", "detector_metadata"
        if "application_service_latency" in combined:
            return "application_service_latency", "detector_metadata"

        detector_name = cls._detector_name(entity=entity, anomaly=anomaly).upper()
        for prefix, family in (
            ("CPU-", "container_cpu"),
            ("RAM-", "container_memory"),
            ("NETLAT-", "network_transport_latency"),
            ("APPLAT-", "application_service_latency"),
        ):
            if detector_name.startswith(prefix):
                return family, "detector_naming_contract"
        return "unknown", "unclassified_detector_metadata"

    @classmethod
    def _affected_component(cls, *, entity: str, anomaly: dict[str, Any]) -> str:
        detector_name = cls._detector_name(entity=entity, anomaly=anomaly)
        upper = detector_name.upper()
        for prefix in ("CPU-", "RAM-"):
            if upper.startswith(prefix) and len(detector_name) > len(prefix):
                return detector_name[len(prefix) :]
        return str(anomaly.get("affected_component") or entity or "unknown").strip()

    @classmethod
    def _build_incident_anchor(
        cls,
        *,
        agent_role: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> dict[str, Any]:
        family, family_source = cls._signal_family(entity=entity, anomaly=anomaly)
        detector_name = cls._detector_name(entity=entity, anomaly=anomaly)
        component = cls._affected_component(entity=entity, anomaly=anomaly)

        guidance: dict[str, tuple[str, list[str]]] = {
            "container_cpu": (
                f"What abnormal process, workload or runtime/resource mechanism on {component} produced the reported CPU anomaly?",
                [
                    "detector-aligned container CPU history around the anomaly",
                    "current container runtime CPU and resource state",
                    "CPU attribution to running processes",
                    "process/thread/tree inspection for the leading CPU consumer",
                    "logs or documented behaviour that can explain that workload",
                ],
            ),
            "container_memory": (
                f"What process, allocation pattern or runtime/resource mechanism on {component} produced the reported memory anomaly?",
                [
                    "detector-aligned container memory history around the anomaly",
                    "current container runtime memory and PID state",
                    "memory attribution to running processes",
                    "process inspection for the leading memory consumer",
                    "logs or documented behaviour that can explain the allocation pattern",
                ],
            ),
            "network_transport_latency": (
                "What transport/path mechanism produced the detector-reported network connection latency anomaly?",
                [
                    "detector-aligned transport-latency history around the anomaly",
                    "name resolution and IP reachability when relevant",
                    "TCP connection behaviour for the affected service path",
                    "socket/path evidence that can localize the transport mechanism",
                    "cross-domain evidence only after transport causes are materially weakened",
                ],
            ),
            "application_service_latency": (
                "What application/service mechanism produced the detector-reported HTTP response latency anomaly?",
                [
                    "detector-aligned application-latency history around the anomaly",
                    "HTTP/service behaviour on the affected request path",
                    "application logs and dependency behaviour around the anomaly",
                    "runtime/process evidence when a workload hypothesis requires it",
                    "cross-domain evidence only when the remaining causal hypothesis requires it",
                ],
            ),
            "unknown": (
                f"What evidence-backed mechanism produced the detector-reported anomaly for {component}?",
                [
                    "measure the detector-reported signal or its closest authoritative telemetry",
                    "collect role-relevant evidence that can discriminate concrete causal hypotheses",
                    "use RAG only to fill static project-fact gaps",
                ],
            ),
        }
        question, priority = guidance[family]

        return {
            "detector_name": detector_name,
            "detector_type": str(anomaly.get("detector_type") or "SINGLE_ENTITY"),
            "detector_description": str(anomaly.get("detector_description") or "").strip() or None,
            "observed_signal": family,
            "signal_classification_source": family_source,
            "measurement_name": str(anomaly.get("measurement_name") or "").strip() or None,
            "feature_name": str(anomaly.get("feature_name") or "").strip() or None,
            "feature_field": str(anomaly.get("feature_field") or "").strip() or None,
            "affected_component": component,
            "reported_entity": entity,
            "data_start_time": anomaly.get("data_start_time"),
            "data_end_time": anomaly.get("data_end_time"),
            "agent_role": agent_role.strip().lower(),
            "primary_diagnostic_question": question,
            "evidence_priority": priority,
            "focus_rule": (
                "Evidence may explain, weaken or localize this signal, but must not replace it "
                "with an unrelated symptom."
            ),
        }

    # ------------------------------------------------------------------
    # Static RAG grounding
    # ------------------------------------------------------------------
    @classmethod
    def _build_initial_rag_query(
        cls,
        *,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> str:
        anchor = cls._build_incident_anchor(
            agent_role=agent_role,
            entity=entity,
            anomaly=anomaly,
        )
        compact_anchor = json.dumps(
            {
                "detector_name": anchor["detector_name"],
                "observed_signal": anchor["observed_signal"],
                "measurement_name": anchor["measurement_name"],
                "feature_field": anchor["feature_field"],
                "affected_component": anchor["affected_component"],
                "primary_diagnostic_question": anchor["primary_diagnostic_question"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        query = (
            "Retrieve authoritative monitored-system knowledge directly relevant to this incident "
            f"anchor: {compact_anchor}. Specialist role: {agent_role.strip().lower()}. "
            f"Severity: {severity}. Prioritize signal-specific troubleshooting guidance, expected "
            "behaviour, configuration and runbooks that help explain the reported signal on the "
            "affected component. Include architecture or service dependencies only when they "
            "directly constrain that causal interpretation. Do not introduce a different symptom, "
            "do not infer current runtime state, and do not replace live diagnostic evidence."
        )
        return query[:2000]

    @staticmethod
    def _grounding_key(assignment: dict[str, Any]) -> tuple[str, str]:
        return (
            str(assignment.get("incident_id") or "").strip(),
            str(assignment.get("task_id") or "").strip(),
        )

    def _knowledge_tool_name(self) -> str | None:
        for name in sorted(self._tool_names):
            normalized = name.strip().lower()
            if normalized == "search_knowledge" or normalized.endswith("search_knowledge"):
                return name
        return None

    def _assignment_with_grounding(self, assignment: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(assignment)
        anomaly = enriched.get("anomaly")
        anomaly_dict = dict(anomaly) if isinstance(anomaly, dict) else {}
        enriched["incident_anchor"] = self._build_incident_anchor(
            agent_role=str(enriched.get("agent_role") or ""),
            entity=str(enriched.get("entity") or "unknown"),
            anomaly=anomaly_dict,
        )
        grounding = self._initial_rag_context_by_task.get(self._grounding_key(enriched))
        if grounding:
            enriched[self._STATIC_GROUNDING_KEY] = grounding
        return enriched

    async def _execute_initial_rag_tool(
        self,
        *,
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
            else {"error": str(wrapper.get("error") or "RAG grounding failed")}
        )
        if success and isinstance(observation, dict):
            status = str(observation.get("status") or "").strip().lower()
            if status in {"error", "failed", "failure"}:
                success = False
        return ReActEvidence(
            step=0,
            tool=tool_name,
            arguments=dict(arguments),
            observation=observation,
            success=success,
        )

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

        item = await self._execute_initial_rag_tool(
            tool_name=tool_name,
            arguments={
                "query": self._build_initial_rag_query(
                    agent_role=agent_role,
                    severity=severity,
                    entity=entity,
                    anomaly=anomaly,
                ),
                "limit": self.INITIAL_RAG_LIMIT,
            },
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

    # ------------------------------------------------------------------
    # Structured Gemma -> Qwen evidence contract
    # ------------------------------------------------------------------
    @classmethod
    def _tool_matches_evidence_kind(cls, tool_name: str, evidence_kind: str) -> bool:
        normalized = str(tool_name or "").strip().lower()
        suffixes = cls._EVIDENCE_KIND_TOOL_SUFFIXES.get(evidence_kind, ())
        return any(normalized == suffix or normalized.endswith(suffix) for suffix in suffixes)

    @classmethod
    def _normalize_and_validate_evidence_request(
        cls,
        request: _EvidenceRequest,
        assignment: dict[str, Any],
    ) -> _EvidenceRequest:
        anchor_raw = assignment.get("incident_anchor")
        anchor = dict(anchor_raw) if isinstance(anchor_raw, dict) else {}
        affected_component = str(anchor.get("affected_component") or "").strip()
        detector_name = str(anchor.get("detector_name") or "").strip()
        reported_entity = str(anchor.get("reported_entity") or "").strip()
        observed_signal = str(anchor.get("observed_signal") or "unknown").strip()

        normalized = request
        if (
            affected_component
            and affected_component != "unknown"
            and request.target_component in {detector_name, reported_entity}
            and request.target_component != affected_component
        ):
            normalized = request.model_copy(update={"target_component": affected_component})

        if observed_signal in cls._RESOURCE_SIGNAL_FAMILIES:
            if (
                affected_component
                and affected_component != "unknown"
                and normalized.target_component != affected_component
                and normalized.causal_relation != "cross_domain_hypothesis"
            ):
                raise ValueError(
                    "resource-anomaly evidence cannot silently pivot from anchor component "
                    f"{affected_component!r} to {normalized.target_component!r}; use "
                    "causal_relation=cross_domain_hypothesis and state the causal link"
                )
            if (
                normalized.kind in cls._RESOURCE_CROSS_DOMAIN_KINDS
                and normalized.causal_relation != "cross_domain_hypothesis"
            ):
                raise ValueError(
                    f"evidence kind {normalized.kind!r} crosses away from a resource anomaly; "
                    "use causal_relation=cross_domain_hypothesis and state how it can produce "
                    "the anchored CPU/memory signal"
                )

        return normalized

    @staticmethod
    def _evidence_request_payload(
        request: _EvidenceRequest,
        assignment: dict[str, Any],
    ) -> dict[str, Any]:
        payload = request.model_dump()
        anchor_raw = assignment.get("incident_anchor")
        anchor = dict(anchor_raw) if isinstance(anchor_raw, dict) else {}
        payload["incident_signal"] = str(anchor.get("observed_signal") or "unknown")
        payload["anchor_component"] = str(anchor.get("affected_component") or "unknown")
        return payload

    def _validate_tool_args(self, tool: Any, args: dict[str, Any]) -> dict[str, Any]:
        clean_args = super()._validate_tool_args(tool, args)
        request = self._active_evidence_request
        if request is None:
            # Compatibility path for injected/offline providers that still emit
            # the historical free-text _ReasoningDecision contract.
            return clean_args

        tool_name = str(getattr(tool, "name", "")).strip()
        if not self._tool_matches_evidence_kind(tool_name, request.kind):
            raise ValueError(
                f"tool {tool_name!r} is incompatible with evidence_request.kind={request.kind!r}; "
                "preserve Gemma's evidence family instead of changing diagnostic direction"
            )

        expected_primary = request.target_component
        for field in self._PRIMARY_TARGET_ARGUMENTS:
            if field not in clean_args:
                continue
            actual = str(clean_args.get(field) or "").strip()
            if actual and actual != expected_primary:
                raise ValueError(
                    f"{field} must preserve evidence_request.target_component={expected_primary!r}; "
                    f"received {actual!r}"
                )

        if request.related_component:
            for field in self._RELATED_TARGET_ARGUMENTS:
                if field not in clean_args:
                    continue
                actual = str(clean_args.get(field) or "").strip()
                if actual and actual != request.related_component:
                    raise ValueError(
                        f"{field} must preserve evidence_request.related_component="
                        f"{request.related_component!r}; received {actual!r}"
                    )
        return clean_args

    # ------------------------------------------------------------------
    # Structured Gemma reasoning
    # ------------------------------------------------------------------
    @staticmethod
    def _ollama_context_size() -> int:
        return _configured_ollama_context()

    @staticmethod
    def _reasoning_json_schema() -> dict[str, Any]:
        return _StructuredReasoningDecision.model_json_schema(mode="validation")

    @staticmethod
    def _normalize_reasoning_payload(payload: Any) -> dict[str, Any]:
        """Keep injected/offline providers compatible with the legacy test contract."""

        if not isinstance(payload, dict):
            raise ValueError("Gemma reasoning output must be a JSON object")
        normalized = dict(payload)
        action = str(normalized.get("action") or "").strip()
        if action == "gather_evidence":
            evidence_needed = str(normalized.get("evidence_needed") or "").strip()
            if not evidence_needed:
                summary = str(normalized.get("decision_summary") or "").strip()
                hypothesis = str(normalized.get("current_hypothesis") or "").strip()
                normalized["evidence_needed"] = (
                    summary
                    or (
                        "Collect live evidence that materially tests this hypothesis: " + hypothesis
                        if hypothesis
                        else "Collect one live observation that materially tests the current assignment."
                    )
                )
        elif action == "finish":
            normalized["evidence_needed"] = None
        return normalized

    async def _provider_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> _ReasoningDecision:
        system_prompt = next(
            (
                str(message.get("content") or "").strip()
                for message in messages
                if str(message.get("role") or "").strip().lower() == "system"
            ),
            "",
        )
        context = ContextManager(system_prompt=system_prompt)
        conversation_id = "specialist-native-structured-reasoning"
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
        return _ReasoningDecision.model_validate(self._normalize_reasoning_payload(raw))

    async def _native_reasoning_request(
        self,
        messages: list[dict[str, str]],
    ) -> Any:
        if not isinstance(self.reasoning_provider, RoleLLMProvider):
            return await self._provider_reasoning_request(messages)

        schema = self._reasoning_json_schema()
        structured_messages = [dict(item) for item in messages]
        structured_messages.insert(
            1 if structured_messages else 0,
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object conforming to the following JSON Schema. "
                    "For gather_evidence, evidence_request is the binding semantic contract passed "
                    "to Qwen; do not name a tool. Do not add prose, Markdown fences, comments, or "
                    "extra fields. JSON Schema: "
                    + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        )
        context_size = self._ollama_context_size()
        payload = {
            "model": self._ollama_model_name(self.reasoning_provider),
            "messages": structured_messages,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": context_size},
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
            done_reason = str(body.get("done_reason") or "unknown")
            raise RuntimeError(
                "Ollama reasoning step returned invalid JSON "
                f"(content_chars={len(content)}, done_reason={done_reason}, num_ctx={context_size})"
            ) from exc

        decision = _StructuredReasoningDecision.model_validate(raw)
        if decision.evidence_request is not None and self._active_reasoning_assignment is not None:
            normalized_request = self._normalize_and_validate_evidence_request(
                decision.evidence_request,
                self._active_reasoning_assignment,
            )
            decision = decision.model_copy(update={"evidence_request": normalized_request})
        return decision

    async def _reason(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> Any:
        grounded_assignment = self._assignment_with_grounding(assignment)
        self._active_reasoning_assignment = grounded_assignment
        try:
            decision = await super()._reason(
                assignment=grounded_assignment,
                evidence=evidence,
                decisions=decisions,
            )
        finally:
            self._active_reasoning_assignment = None

        request = getattr(decision, "evidence_request", None)
        self._active_evidence_request = request if isinstance(request, _EvidenceRequest) else None
        self._bounded_evidence_snapshot = list(evidence)
        self._bounded_decisions_snapshot = [*decisions, decision]
        return decision

    async def _select_tool(
        self,
        *,
        assignment: dict[str, Any],
        evidence_needed: str,
        evidence: list[ReActEvidence],
    ) -> tuple[str, dict[str, Any]]:
        grounded_assignment = self._assignment_with_grounding(assignment)
        if self._active_evidence_request is not None:
            grounded_assignment["evidence_request"] = self._evidence_request_payload(
                self._active_evidence_request,
                grounded_assignment,
            )
        self._active_selection_assignment = grounded_assignment
        return await super()._select_tool(
            assignment=grounded_assignment,
            evidence_needed=evidence_needed,
            evidence=evidence,
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
        if action == "select_tool" and self._active_evidence_request is not None:
            assignment = self._active_selection_assignment or {}
            enriched["evidence_request"] = self._evidence_request_payload(
                self._active_evidence_request,
                assignment,
            )
            enriched["semantic_contract_enforced"] = True
        await super()._emit_trace(
            action=action,
            reason=reason,
            incident_id=incident_id,
            task_id=task_id,
            outcome=outcome,
            tool=tool,
            details=enriched,
        )

    # ------------------------------------------------------------------
    # Diagnostic finalization
    # ------------------------------------------------------------------
    def _build_finalizer(self) -> _ContextAwarePromptGemmaDiagnosticFinalizer:
        return _ContextAwarePromptGemmaDiagnosticFinalizer(
            model=self._ollama_model_name(self.reasoning_provider),
            base_url=self._ollama_base_url(self.reasoning_provider),
            timeout_seconds=max(60.0, self.tool_timeout_seconds * 4),
            context_size=self._ollama_context_size(),
        )

    async def _finalize(
        self,
        *,
        assignment: dict[str, Any],
        evidence: list[ReActEvidence],
        decisions: list[Any],
    ) -> _PromptDiagnosticFinalOutput:
        grounded_assignment = self._assignment_with_grounding(assignment)
        reasoning_evidence = self._project_evidence_for_reasoning(evidence)
        prompt = (
            f"{self.FINALIZATION_POLICY}\n\n"
            "Assignment (including incident anchor and static grounding when available):\n"
            f"{json.dumps(grounded_assignment, default=str, ensure_ascii=False)}\n\n"
            "Operational reasoning summaries:\n"
            f"{json.dumps([item.model_dump() for item in decisions], default=str, ensure_ascii=False)}\n\n"
            "Collected tool evidence:\n"
            f"{json.dumps([item.to_dict() for item in reasoning_evidence], default=str, ensure_ascii=False)}"
        )
        current_domain = _ROLE_DOMAIN.get(
            str(grounded_assignment.get("agent_role") or "").strip().lower()
        )

        last_error: Exception | None = None
        validation_feedback = ""
        for _attempt in range(3):
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
                        f"{grounded_assignment.get('agent_role')} cannot request assistance from its "
                        f"own domain {current_domain!r}; choose a different peer domain or null"
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

    # ------------------------------------------------------------------
    # Bounded saturation recovery
    # ------------------------------------------------------------------
    @classmethod
    def _is_duplicate_selection_error(cls, error: Exception) -> bool:
        return cls._DUPLICATE_SELECTION_MARKER in str(error).lower()

    @classmethod
    def _is_concrete_cause(cls, value: Any) -> bool:
        return str(value or "").strip().lower() not in cls._EMPTY_CAUSE_MARKERS

    @classmethod
    def _best_available_hypothesis(cls, decisions: list[Any]) -> str | None:
        for decision in reversed(decisions):
            hypothesis = str(decision.current_hypothesis or "").strip()
            if cls._is_concrete_cause(hypothesis):
                return hypothesis
        return None

    async def _finalize_evidence_saturation(
        self,
        *,
        task_id: str,
        incident_id: str,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
        error: ReActInvestigationError,
    ) -> ReActInvestigationResult:
        evidence = list(self._bounded_evidence_snapshot)
        decisions = list(self._bounded_decisions_snapshot)
        if not evidence:
            raise error

        assignment = {
            "task_id": task_id,
            "incident_id": incident_id,
            "agent_role": agent_role,
            "severity": severity,
            "entity": entity,
            "anomaly": anomaly,
        }
        decisions.append(
            _ReasoningDecision(
                action="finish",
                decision_summary=(
                    "The action selector cannot add a new discriminating observation without "
                    "repeating an already successful call. Finalize strictly from retained evidence; "
                    "do not promote uncertainty merely to close the bounded run."
                ),
                current_hypothesis=self._best_available_hypothesis(decisions),
                evidence_needed=None,
            )
        )
        await self._emit_trace(
            action="evidence_saturation",
            reason=(
                "Qwen repeatedly selected an already successful equivalent diagnostic action; "
                "Gemma will finalize the retained evidence without semantic promotion."
            ),
            incident_id=incident_id,
            task_id=task_id,
            outcome="finalize_without_semantic_promotion",
            details={
                "react_steps": min(len(decisions), self.max_steps),
                "successful_evidence_count": sum(1 for item in evidence if item.success),
                "selection_error": str(error),
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
                "semantic_promotion": False,
            },
        )

        normalized_role = agent_role.strip().lower()
        conversation_id = f"react:{normalized_role}:{incident_id}:{task_id}"
        self.context.add_assistant_message(output.model_dump_json(), conversation_id)
        tools_used: list[str] = []
        for item in evidence:
            if item.tool not in tools_used:
                tools_used.append(item.tool)

        return ReActInvestigationResult(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=normalized_role,
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

    # ------------------------------------------------------------------
    # Public investigation entry point
    # ------------------------------------------------------------------
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
        normalized_incident_id = incident_id.strip()
        normalized_agent_role = agent_role.strip().lower()
        if not normalized_task_id or not normalized_incident_id or not normalized_agent_role:
            raise ValueError("ReAct investigation requires task, incident and agent identity")

        anchor = self._build_incident_anchor(
            agent_role=normalized_agent_role,
            entity=entity,
            anomaly=anomaly,
        )
        await self._emit_trace(
            action="incident_anchor",
            reason="Bound the investigation to the detector-reported anomaly signal before RAG and ReAct.",
            incident_id=normalized_incident_id,
            task_id=normalized_task_id,
            outcome=(
                f"signal={anchor['observed_signal']}; component={anchor['affected_component']}; "
                "focus_locked=true; react_budget_consumed=false"
            ),
            details={**anchor, "react_budget_consumed": False, "runtime_evidence": False},
        )

        grounding_key = (normalized_incident_id, normalized_task_id)
        grounding_item: ReActEvidence | None = None
        self._bounded_evidence_snapshot = []
        self._bounded_decisions_snapshot = []
        self._active_reasoning_assignment = None
        self._active_selection_assignment = None
        self._active_evidence_request = None

        try:
            grounding_item = await self._retrieve_initial_rag_grounding(
                task_id=normalized_task_id,
                incident_id=normalized_incident_id,
                agent_role=normalized_agent_role,
                severity=severity,
                entity=entity,
                anomaly=anomaly,
            )
            if grounding_item is not None and grounding_item.success:
                self._initial_rag_context_by_task[grounding_key] = {
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

            try:
                result = await super().investigate(
                    task_id=normalized_task_id,
                    incident_id=normalized_incident_id,
                    agent_role=normalized_agent_role,
                    severity=severity,
                    entity=entity,
                    anomaly=anomaly,
                )
            except ReActInvestigationError as exc:
                if not self._is_duplicate_selection_error(exc):
                    raise
                result = await self._finalize_evidence_saturation(
                    task_id=normalized_task_id,
                    incident_id=normalized_incident_id,
                    agent_role=normalized_agent_role,
                    severity=severity,
                    entity=entity,
                    anomaly=anomaly,
                    error=exc,
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
            self._initial_rag_context_by_task.pop(grounding_key, None)
            self._active_reasoning_assignment = None
            self._active_selection_assignment = None
            self._active_evidence_request = None
