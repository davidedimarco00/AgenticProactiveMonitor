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


def test_native_schema_exposes_structured_request_instead_of_free_text_evidence_needed() -> None:
    schema = SpecialistReActExecutor._reasoning_json_schema()
    properties = schema["properties"]

    assert "evidence_request" in properties
    assert "evidence_request" in schema["required"]
    assert "evidence_needed" not in properties
