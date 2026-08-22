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
