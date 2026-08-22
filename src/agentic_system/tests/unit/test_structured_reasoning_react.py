from agentic_system.agents.factory import MAX_DIAGNOSTIC_REACT_STEPS
from agentic_system.reasoning.langchain_agent import ReActInvestigationError, _ReasoningDecision
from agentic_system.reasoning.structured_reasoning_react import SpecialistReActExecutor


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


def test_specialist_react_is_capped_at_six_cycles() -> None:
    assert MAX_DIAGNOSTIC_REACT_STEPS == 6
