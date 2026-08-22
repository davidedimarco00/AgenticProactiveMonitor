from agentic_system.agents.factory import MAX_DIAGNOSTIC_REACT_STEPS
from agentic_system.reasoning.diagnostic_react import _DiagnosticFinalOutput
from agentic_system.reasoning.langchain_agent import (
    ReActEvidence,
    ReActInvestigationError,
    _ReasoningDecision,
)
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


def test_best_effort_gate_requires_live_not_rag_only_evidence() -> None:
    rag = ReActEvidence(
        step=1,
        tool="apm_mcp_search_knowledge",
        arguments={"query": "processing-service CPU behaviour"},
        observation={"results": ["static project knowledge"]},
        success=True,
    )
    live = ReActEvidence(
        step=2,
        tool="apm_mcp_get_processes",
        arguments={"host_id": "processing-service"},
        observation={"processes": [{"pid": 42, "cpu": 98.0}]},
        success=True,
    )

    assert SpecialistReActExecutor._has_successful_live_evidence([rag]) is False
    assert SpecialistReActExecutor._has_successful_live_evidence([rag, live]) is True


def test_best_effort_gate_recognizes_six_step_budget_exhaustion() -> None:
    decisions = [
        _ReasoningDecision(
            action="gather_evidence",
            decision_summary=f"Diagnostic step {step}.",
            current_hypothesis="CPU-intensive Python child processes are causing saturation.",
            evidence_needed="Collect another discriminating live observation.",
        )
        for step in range(MAX_DIAGNOSTIC_REACT_STEPS)
    ]

    assert SpecialistReActExecutor._is_bounded_closure(
        decisions,
        MAX_DIAGNOSTIC_REACT_STEPS,
    ) is True


def test_best_effort_gate_recognizes_evidence_saturation_before_step_limit() -> None:
    decisions = [
        _ReasoningDecision(
            action="finish",
            decision_summary=(
                "The autonomous evidence path is saturated, so close with the best diagnosis."
            ),
            current_hypothesis="CPU-intensive Python child processes are causing saturation.",
            evidence_needed=None,
        )
    ]

    assert SpecialistReActExecutor._is_bounded_closure(
        decisions,
        MAX_DIAGNOSTIC_REACT_STEPS,
    ) is True


def test_bounded_live_cpu_evidence_is_promoted_to_probable_diagnosis() -> None:
    evidence = [
        ReActEvidence(
            step=1,
            tool="apm_mcp_get_runtime_stats",
            arguments={"host_id": "processing-service"},
            observation={"cpu": {"usage_percent": 1200.81}},
            success=True,
        ),
        ReActEvidence(
            step=2,
            tool="apm_mcp_get_processes",
            arguments={"host_id": "processing-service", "limit": 20},
            observation={
                "processes": [
                    {"pid": 23154, "command": "python3", "cpu_percent": 99.9},
                    {"pid": 23155, "command": "python3", "cpu_percent": 99.9},
                ]
            },
            success=True,
        ),
    ]
    decisions = [
        _ReasoningDecision(
            action="finish",
            decision_summary="The bounded evidence path is saturated.",
            current_hypothesis=(
                "Multiple CPU-intensive python3 child processes are causing CPU saturation "
                "in processing-service."
            ),
            evidence_needed=None,
        )
    ]
    inconclusive = _DiagnosticFinalOutput(
        summary="High CPU is sustained by multiple Python processes.",
        diagnosis_status="inconclusive",
        root_cause=None,
        causal_chain=[
            "Multiple python3 child processes consume nearly one CPU each.",
            "Container CPU usage rises far above the normal application workload.",
        ],
        confidence=0.8,
        findings=["Multiple python3 child processes are near 100% CPU."],
        hypotheses=[
            "Multiple CPU-intensive python3 child processes are causing CPU saturation "
            "in processing-service."
        ],
        recommended_next_steps=["Inspect the parent workload that spawned the CPU-bound children."],
        assistance_required=False,
        assistance_domain=None,
    )

    promoted = SpecialistReActExecutor._promote_bounded_best_effort(
        output=inconclusive,
        assignment={"entity": "CPU-processing-service"},
        evidence=evidence,
        decisions=decisions,
    )

    assert promoted.diagnosis_status == "probable"
    assert promoted.root_cause == (
        "Multiple CPU-intensive python3 child processes are causing CPU saturation "
        "in processing-service."
    )
    assert promoted.causal_chain
    assert promoted.confidence == 0.8
    assert promoted.assistance_required is False


def test_bounded_best_effort_does_not_promote_rag_only_evidence() -> None:
    evidence = [
        ReActEvidence(
            step=1,
            tool="apm_mcp_search_knowledge",
            arguments={"query": "CPU spike"},
            observation={"results": ["static runbook"]},
            success=True,
        )
    ]
    decisions = [
        _ReasoningDecision(
            action="finish",
            decision_summary="The bounded evidence path is saturated.",
            current_hypothesis="A CPU-bound worker may be responsible.",
            evidence_needed=None,
        )
    ]
    inconclusive = _DiagnosticFinalOutput(
        summary="Only static knowledge was retrieved.",
        diagnosis_status="inconclusive",
        root_cause=None,
        causal_chain=[],
        confidence=0.4,
        findings=[],
        hypotheses=["A CPU-bound worker may be responsible."],
        recommended_next_steps=[],
        assistance_required=False,
        assistance_domain=None,
    )

    result = SpecialistReActExecutor._promote_bounded_best_effort(
        output=inconclusive,
        assignment={"entity": "CPU-processing-service"},
        evidence=evidence,
        decisions=decisions,
    )

    assert result.diagnosis_status == "inconclusive"
    assert result.root_cause is None


def test_specialist_react_is_capped_at_six_cycles() -> None:
    assert MAX_DIAGNOSTIC_REACT_STEPS == 6
