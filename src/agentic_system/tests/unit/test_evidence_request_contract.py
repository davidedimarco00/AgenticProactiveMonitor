from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentic_system.reasoning.react_contracts import (
    _EvidenceRequest,
    _StructuredReasoningDecision,
)
from agentic_system.reasoning.specialist_react import SpecialistReActExecutor


def _cpu_assignment() -> dict:
    return {
        "incident_anchor": SpecialistReActExecutor._build_incident_anchor(
            agent_role="system_engineer",
            entity="CPU-processing-service",
            anomaly={"detector_name": "CPU-processing-service"},
        )
    }


def _request(
    *,
    kind: str = "process_attribution",
    target: str = "processing-service",
    related: str | None = None,
    relation: str = "attribute_cause",
) -> _EvidenceRequest:
    return _EvidenceRequest(
        kind=kind,
        target_component=target,
        related_component=related,
        purpose="Identify the runtime workload responsible for the reported CPU consumption.",
        time_scope="current",
        causal_relation=relation,
        causal_link=(
            "The requested observation tests whether the candidate workload can account for "
            "the detector-reported CPU signal."
        ),
    )


def _selection_assignment(request: _EvidenceRequest) -> dict:
    assignment = _cpu_assignment()
    assignment["evidence_request"] = {
        **request.model_dump(),
        "incident_signal": "container_cpu",
        "anchor_component": "processing-service",
    }
    return assignment


def test_gather_evidence_requires_structured_evidence_request() -> None:
    with pytest.raises(ValidationError, match="gather_evidence requires evidence_request"):
        _StructuredReasoningDecision(
            action="gather_evidence",
            decision_summary="CPU attribution is still required.",
            current_hypothesis="A CPU-bound workload may be active.",
            evidence_request=None,
        )


def test_evidence_needed_is_derived_from_structured_request() -> None:
    request = _request()
    decision = _StructuredReasoningDecision(
        action="gather_evidence",
        decision_summary="Attribute CPU usage to a concrete workload.",
        current_hypothesis="A CPU-bound workload may be active.",
        evidence_request=request,
    )

    assert decision.evidence_needed == request.purpose
    assert decision.model_dump()["evidence_request"]["kind"] == "process_attribution"


def test_cpu_request_cannot_silently_change_component() -> None:
    request = _request(target="api-gateway")

    with pytest.raises(ValueError, match="cannot silently pivot"):
        SpecialistReActExecutor._normalize_and_validate_evidence_request(
            request,
            _cpu_assignment(),
        )


def test_cpu_request_cannot_silently_pivot_to_network_evidence() -> None:
    request = _request(kind="network_path", relation="test_hypothesis")

    with pytest.raises(ValueError, match="crosses away from a resource anomaly"):
        SpecialistReActExecutor._normalize_and_validate_evidence_request(
            request,
            _cpu_assignment(),
        )


def test_explicit_cross_domain_cpu_hypothesis_can_request_network_evidence() -> None:
    request = _request(
        kind="network_path",
        related="data-service",
        relation="cross_domain_hypothesis",
    )

    normalized = SpecialistReActExecutor._normalize_and_validate_evidence_request(
        request,
        _cpu_assignment(),
    )
    assert normalized.kind == "network_path"
    assert normalized.causal_relation == "cross_domain_hypothesis"


def test_tool_family_contract_rejects_tcp_for_process_attribution() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    executor._active_evidence_request = _request()
    tool = SimpleNamespace(name="apm_mcp_test_tcp_connection")

    with pytest.raises(ValueError, match="incompatible with evidence_request.kind"):
        executor._validate_tool_semantics(tool, {"host_id": "processing-service"})


def test_tool_family_contract_accepts_process_tool_and_preserves_target() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    executor._active_evidence_request = _request()
    tool = SimpleNamespace(name="apm_mcp_get_processes")

    assert executor._validate_tool_semantics(
        tool,
        {"host_id": "processing-service"},
    ) == {"host_id": "processing-service"}

    with pytest.raises(ValueError, match="must preserve evidence_request.target_component"):
        executor._validate_tool_semantics(tool, {"host_id": "api-gateway"})


def test_runtime_binding_replaces_qwen_wrong_primary_target() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    request = _request(kind="runtime_resource_state")
    tool = SimpleNamespace(
        name="apm_mcp_get_runtime_stats",
        args_schema={
            "type": "object",
            "properties": {
                "host_id": {
                    "type": "string",
                    "enum": [
                        "traffic-generator",
                        "api-gateway",
                        "processing-service",
                        "data-service",
                        "worker-service",
                    ],
                }
            },
            "required": ["host_id"],
            "additionalProperties": False,
        },
    )

    bound = executor._bind_tool_arguments(
        tool,
        {"host_id": "api-gateway"},
        _selection_assignment(request),
    )

    assert bound == {"host_id": "processing-service"}
    assert executor._validate_tool_args(tool, bound) == {
        "host_id": "processing-service"
    }


def test_runtime_binding_supplies_missing_primary_target_before_schema_validation() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    request = _request(kind="process_attribution")
    tool = SimpleNamespace(
        name="apm_mcp_get_processes",
        args_schema={
            "type": "object",
            "properties": {
                "host_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["host_id"],
            "additionalProperties": False,
        },
    )

    bound = executor._bind_tool_arguments(
        tool,
        {"limit": 10},
        _selection_assignment(request),
    )

    assert bound == {"limit": 10, "host_id": "processing-service"}
    assert executor._validate_tool_args(tool, bound) == bound


def test_runtime_binding_preserves_explicit_cross_domain_path_targets() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    request = _request(
        kind="network_path",
        target="processing-service",
        related="data-service",
        relation="cross_domain_hypothesis",
    )
    tool = SimpleNamespace(
        name="apm_mcp_test_tcp_connection",
        args_schema={
            "type": "object",
            "properties": {
                "host_id": {"type": "string"},
                "target_host": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["host_id", "target_host"],
            "additionalProperties": False,
        },
    )

    bound = executor._bind_tool_arguments(
        tool,
        {"host_id": "api-gateway", "target_host": "api-gateway", "timeout_seconds": 2},
        _selection_assignment(request),
    )

    assert bound["host_id"] == "processing-service"
    assert bound["target_host"] == "data-service"
    assert bound["timeout_seconds"] == 2


def test_single_process_attribution_tool_is_selected_without_qwen_arguments() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    request = _request(kind="process_attribution")
    executor._active_evidence_request = request
    tool = SimpleNamespace(
        name="apm_mcp_get_processes",
        args_schema={
            "type": "object",
            "properties": {
                "host_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["host_id"],
            "additionalProperties": False,
        },
    )
    executor._tool_names = {"apm_mcp_get_processes", "apm_mcp_get_runtime_stats"}
    executor._langchain_tool_by_name = {"apm_mcp_get_processes": tool}
    assignment = _selection_assignment(request)

    assert executor._candidate_tool_names_for_request(assignment) == (
        "apm_mcp_get_processes",
    )
    assert executor._deterministic_single_tool_arguments(
        tool=tool,
        assignment=assignment,
        evidence_needed=request.purpose,
    ) == {"host_id": "processing-service"}


def test_single_metric_history_tool_derives_cpu_metric_from_incident_anchor() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    request = _request(kind="metric_history", relation="measure_signal")
    executor._active_evidence_request = request
    tool = SimpleNamespace(
        name="apm_mcp_get_metrics",
        args_schema={
            "type": "object",
            "properties": {
                "host_id": {"type": "string"},
                "metric": {"type": "string", "enum": ["cpu", "memory"]},
                "minutes": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["host_id", "metric"],
            "additionalProperties": False,
        },
    )
    executor._tool_names = {"apm_mcp_get_metrics", "apm_mcp_get_runtime_stats"}
    executor._langchain_tool_by_name = {"apm_mcp_get_metrics": tool}
    assignment = _selection_assignment(request)

    assert executor._candidate_tool_names_for_request(assignment) == (
        "apm_mcp_get_metrics",
    )
    assert executor._deterministic_single_tool_arguments(
        tool=tool,
        assignment=assignment,
        evidence_needed=request.purpose,
    ) == {
        "host_id": "processing-service",
        "metric": "cpu",
    }


def test_network_path_keeps_qwen_when_multiple_tools_are_compatible() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    request = _request(
        kind="network_path",
        related="data-service",
        relation="cross_domain_hypothesis",
    )
    executor._tool_names = {
        "apm_mcp_get_network_connections",
        "apm_mcp_resolve_service_dns",
        "apm_mcp_test_tcp_connection",
        "apm_mcp_test_icmp_reachability",
    }

    assert len(executor._candidate_tool_names_for_request(_selection_assignment(request))) == 4


def test_native_schema_exposes_structured_request_instead_of_free_text_evidence_needed() -> None:
    schema = SpecialistReActExecutor._reasoning_json_schema()
    properties = schema["properties"]

    assert "evidence_request" in properties
    assert "evidence_request" in schema["required"]
    assert "evidence_needed" not in properties
