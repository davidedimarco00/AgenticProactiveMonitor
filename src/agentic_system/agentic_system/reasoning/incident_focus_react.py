from __future__ import annotations

import json
from typing import Any

from .context_robust_react import SpecialistReActExecutor as _ContextRobustExecutor


class SpecialistReActExecutor(_ContextRobustExecutor):
    """Context-robust ReAct with an immutable incident-focus contract.

    The detector observation is the starting symptom of the investigation. RAG,
    live observations and cross-domain evidence may explain or weaken hypotheses,
    but they must not silently replace that symptom with an unrelated one.

    This layer also removes the MCP server health-check ``ping`` from the
    specialist action space. Real network diagnosis remains available through
    ``test_icmp_reachability`` and the other bounded diagnostic tools.
    """

    _HEALTH_ONLY_TOOL_NAMES = {"ping", "apm_mcp_ping"}

    REASONING_POLICY = (
        _ContextRobustExecutor.REASONING_POLICY
        + """

Incident-anchor invariant:
- The assignment contains an incident_anchor describing the detector-reported signal. Treat that
  reported anomaly signal as INVARIANT throughout this investigation: explain it, test it, refine
  its cause, or show that it is no longer present, but never replace it with an unrelated symptom.
- Every new evidence request must do at least one of these: (1) measure the anchored signal or its
  relevant history, (2) test a concrete causal hypothesis that could produce the anchored signal,
  (3) identify the component/process responsible for it, or (4) establish with evidence that the
  remaining causal hypothesis belongs to another specialist domain.
- Use incident_anchor.evidence_priority as diagnostic guidance, not as a mandatory scripted tool
  sequence. You remain responsible for choosing the most discriminating next evidence.
- Static RAG topology, dependencies and runbooks are supporting context. They MUST NOT change which
  quantity OpenSearch reported as anomalous and MUST NOT create a second anomaly that was not observed.
- Off-domain evidence is secondary. Do not pivot from a CPU/memory symptom to network checks, from a
  network symptom to resource checks, or between other domains unless a stated causal hypothesis
  explicitly connects that check to the anchored signal and primary-domain evidence justifies it.
- If a current observation is healthy while the detector reported an earlier anomaly, treat this as
  temporal evidence. Prefer checking the anomaly window/history or the mechanism that could have been
  transient; do not use an unrelated healthy subsystem as the explanation.
"""
    ).strip()

    TOOL_SELECTION_POLICY = (
        _ContextRobustExecutor.TOOL_SELECTION_POLICY
        + """

Incident-anchor action policy:
- The assignment's incident_anchor is authoritative focus context for action selection.
- Select a tool only when its observation directly addresses Gemma's evidence request AND remains
  causally relevant to incident_anchor.primary_diagnostic_question.
- Prefer evidence that measures the detector-reported quantity or attributes it to a causal
  component/process before unrelated subsystem checks.
- RAG context and healthy observations may narrow hypotheses but do not authorize a silent pivot to
  another symptom or domain.
- The generic MCP server health-check named ping is not an incident diagnostic action and is excluded
  from the specialist tool set. ICMP troubleshooting, when causally relevant, uses the dedicated
  bounded test_icmp_reachability tool.
"""
    ).strip()

    FINALIZATION_POLICY = (
        _ContextRobustExecutor.FINALIZATION_POLICY
        + """

Incident-anchor closure policy:
- The final root cause and causal_chain must explain incident_anchor.observed_signal, not a different
  healthy or anomalous quantity discovered during troubleshooting.
- Evidence about another subsystem may appear in findings or eliminate hypotheses, but it cannot
  replace the detector-reported symptom as the diagnostic question.
- If the anchored symptom cannot be causally explained within the bounded investigation, preserve an
  inconclusive outcome or request a causally relevant peer; never manufacture coherence by switching
  to a different symptom.
"""
    ).strip()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        tools = kwargs.get("tools")
        if tools is not None:
            kwargs["tools"] = self._filter_specialist_tools(list(tools))
        super().__init__(*args, **kwargs)

    @classmethod
    def _filter_specialist_tools(cls, tools: list[Any]) -> list[Any]:
        """Remove control-plane health probes from the diagnostic action space."""

        filtered: list[Any] = []
        for tool in tools:
            name = str(getattr(tool, "name", "")).strip().lower()
            if name in cls._HEALTH_ONLY_TOOL_NAMES:
                continue
            filtered.append(tool)
        return filtered

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
        """Resolve the diagnostic signal without LLM interpretation.

        Exact detector metadata wins when present. The detector-name prefix is a
        bounded fallback for older persisted/synthetic observations created by
        this project, whose detector naming contract is deterministic.
        """

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
        prefix_map = (
            ("CPU-", "container_cpu"),
            ("RAM-", "container_memory"),
            ("NETLAT-", "network_transport_latency"),
            ("APPLAT-", "application_service_latency"),
        )
        for prefix, family in prefix_map:
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

    @classmethod
    def _build_initial_rag_query(
        cls,
        *,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ) -> str:
        """Retrieve signal-specific static context instead of broad topology first."""

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

    def _assignment_with_grounding(self, assignment: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(super()._assignment_with_grounding(assignment))
        anomaly = enriched.get("anomaly")
        anomaly_dict = dict(anomaly) if isinstance(anomaly, dict) else {}
        enriched["incident_anchor"] = self._build_incident_anchor(
            agent_role=str(enriched.get("agent_role") or ""),
            entity=str(enriched.get("entity") or "unknown"),
            anomaly=anomaly_dict,
        )
        return enriched

    async def investigate(
        self,
        *,
        task_id: str,
        incident_id: str,
        agent_role: str,
        severity: str,
        entity: str,
        anomaly: dict[str, Any],
    ):
        anchor = self._build_incident_anchor(
            agent_role=agent_role,
            entity=entity,
            anomaly=anomaly,
        )
        await self._emit_trace(
            action="incident_anchor",
            reason="Bound the investigation to the detector-reported anomaly signal before RAG and ReAct.",
            incident_id=incident_id,
            task_id=task_id,
            outcome=(
                f"signal={anchor['observed_signal']}; component={anchor['affected_component']}; "
                "focus_locked=true; react_budget_consumed=false"
            ),
            details={
                **anchor,
                "react_budget_consumed": False,
                "runtime_evidence": False,
            },
        )
        return await super().investigate(
            task_id=task_id,
            incident_id=incident_id,
            agent_role=agent_role,
            severity=severity,
            entity=entity,
            anomaly=anomaly,
        )
