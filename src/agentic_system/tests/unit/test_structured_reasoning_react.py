from agentic_system.agents.factory import MAX_DIAGNOSTIC_REACT_STEPS
from agentic_system.reasoning import SpecialistReActExecutor
from agentic_system.reasoning.langchain_agent import (
    ReActEvidence,
    ReActInvestigationError,
    _ReasoningDecision,
)


def test_reasoning_schema_requires_evidence_needed_field() -> None:
    schema = SpecialistReActExecutor._reasoning_json_schema()

    assert "evidence_needed" in schema["required"]


def test_missing_evidence_needed_is_recovered_from_decision_summary() -> None:
    normalized = SpecialistReActExecutor._normalize_reasoning_payload(
        {
            "action": "gather_evidence",
            "decision_summary": "Verify current availability of the agentic MCP server.",
            "current_hypothesis": "The MCP server may be unavailable.",
        }
    )

    assert normalized["evidence_needed"] == (
        "Verify current availability of the agentic MCP server."
    )


def test_finish_forces_evidence_needed_to_null() -> None:
    normalized = SpecialistReActExecutor._normalize_reasoning_payload(
        {
            "action": "finish",
            "decision_summary": "The evidence supports diagnostic closure.",
            "current_hypothesis": "A CPU-bound worker is causing saturation.",
            "evidence_needed": "This stale request must not survive closure.",
        }
    )

    assert normalized["evidence_needed"] is None


def test_duplicate_tool_selection_is_classified_as_evidence_saturation() -> None:
    error = ReActInvestigationError(
        "Qwen tool selection failed: The same successful diagnostic call was already executed; "
        "select a different action that adds discriminating evidence."
    )

    assert SpecialistReActExecutor._is_duplicate_selection_error(error) is True


def test_best_available_hypothesis_uses_latest_nonempty_candidate() -> None:
    decisions = [
        _ReasoningDecision(
            action="gather_evidence",
            decision_summary="Confirm the CPU anomaly.",
            current_hypothesis="CPU saturation may be caused by a CPU-bound worker.",
            evidence_needed="Live CPU evidence.",
        ),
        _ReasoningDecision(
            action="gather_evidence",
            decision_summary="Inspect the suspected workload.",
            current_hypothesis="A CPU-bound worker process is saturating processing-service.",
            evidence_needed="Process-level CPU evidence.",
        ),
    ]

    assert SpecialistReActExecutor._best_available_hypothesis(decisions) == (
        "A CPU-bound worker process is saturating processing-service."
    )


def test_nonempty_hypothesis_is_context_not_python_validated_root_cause() -> None:
    decisions = [
        _ReasoningDecision(
            action="finish",
            decision_summary="Local checks are complete.",
            current_hypothesis="The service is running and handling requests.",
            evidence_needed=None,
        )
    ]

    # Python may preserve the latest text as context for Gemma, but it does not
    # interpret this normal-state observation as a valid causal diagnosis.
    assert SpecialistReActExecutor._best_available_hypothesis(decisions) == (
        "The service is running and handling requests."
    )
    assert "_promote_bounded_best_effort" not in SpecialistReActExecutor.__dict__


def test_hard_stop_with_live_evidence_remains_inconclusive() -> None:
    evidence = [
        ReActEvidence(
            step=1,
            tool="apm_mcp_get_runtime_stats",
            arguments={"host_id": "processing-service"},
            observation={"cpu": {"usage_percent": 1200.81}},
            success=True,
        )
    ]
    decisions = [
        _ReasoningDecision(
            action="finish",
            decision_summary="The bounded local evidence path ended.",
            current_hypothesis="A CPU-bound worker may be causing saturation.",
            evidence_needed=None,
        )
    ]

    output = SpecialistReActExecutor._hard_stop_output(
        evidence=evidence,
        decisions=decisions,
        reason="finalizer could not produce a schema-valid causal diagnosis",
    )

    assert output.diagnosis_status == "inconclusive"
    assert output.root_cause is None
    assert output.assistance_required is False
    assert output.assistance_domain is None


def test_consolidated_executor_keeps_bounded_semantics_without_probable_promotion() -> None:
    assert "_promote_bounded_best_effort" not in SpecialistReActExecutor.__dict__
    assert "_is_bounded_closure" not in SpecialistReActExecutor.__dict__
    assert "_has_successful_live_evidence" not in SpecialistReActExecutor.__dict__
    assert "_finalize" in SpecialistReActExecutor.__dict__
    assert "_retrieve_initial_rag_grounding" in SpecialistReActExecutor.__dict__
    assert "_build_incident_anchor" in SpecialistReActExecutor.__dict__


def test_specialist_react_is_capped_at_six_cycles() -> None:
    assert MAX_DIAGNOSTIC_REACT_STEPS == 6
