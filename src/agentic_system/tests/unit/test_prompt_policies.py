import pytest

from agentic_system.agents.prompts import SPECIALIST_ROLE_PROFILES, specialist_system_prompt
from agentic_system.agents.review import TechnicalLeadReviewReasoner
from agentic_system.reasoning import SpecialistReActExecutor


def _normalized(text: str) -> str:
    """Normalize formatting-only whitespace before checking prompt policy semantics."""

    return " ".join(text.split())


@pytest.mark.parametrize("role", sorted(SPECIALIST_ROLE_PROFILES))
def test_specialist_prompt_is_role_specific_and_evidence_first(role: str) -> None:
    prompt = specialist_system_prompt(role)
    normalized_prompt = _normalized(prompt)
    profile = SPECIALIST_ROLE_PROFILES[role]

    assert profile["display_name"] in prompt
    assert profile["priority"] in prompt
    assert profile["boundary"] in prompt
    assert "live MCP observations" in normalized_prompt
    assert "RAG knowledge alone is not evidence" in normalized_prompt
    assert "specific missing evidence" in normalized_prompt
    assert "A symptom is not a root cause" in normalized_prompt
    assert "Healthy observations" in normalized_prompt
    assert "never from the detector name alone" in normalized_prompt


def test_specialist_prompt_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="Unsupported specialist role"):
        specialist_system_prompt("database_engineer")


def test_tool_selection_policy_separates_live_evidence_from_rag() -> None:
    policy = _normalized(SpecialistReActExecutor.TOOL_SELECTION_POLICY)

    assert "exactly ONE" in policy
    assert "Live runtime claims" in policy
    assert "RAG/project knowledge" in policy
    assert "cannot prove" in policy
    assert "Do not repeat" in policy
    assert "Do not diagnose" in policy
    assert "success=false is a failed evidence acquisition" in policy
    assert "Respect every bound declared by the tool schema" in policy
    assert "repair the arguments" in policy
    assert "Service ports are authoritative topology facts" in policy
    assert "NOT an LLM decision" in policy
    assert "MCP tool resolves the target's internal container port deterministically" in policy
    assert "Never add, infer, copy, guess or transfer a host-published port" in policy
    assert "Distinguish Docker host-published ports from internal service ports" in policy
    assert "must never be reassigned to another component" in policy
    assert "failed check against an unverified identifier is not evidence" in policy


def test_reasoning_policy_distinguishes_symptom_normal_state_and_root_cause() -> None:
    policy = _normalized(SpecialistReActExecutor.REASONING_POLICY)

    assert "objective is CAUSAL DIAGNOSIS" in policy
    assert "is NOT by itself a root cause" in policy
    assert "normal-state observation" in policy
    assert "Healthy observations are elimination evidence" in policy
    assert "candidate cause -> intermediate effect(s) -> reported anomaly" in policy
    assert "do not invent a local root cause" in policy
    assert "never from the detector name alone" in policy
    assert "bounded execution budget is not diagnostic evidence" in policy
    assert "Never convert uncertainty into probable" in policy
    assert "Diagnostic-tool failure policy" in policy
    assert "failure of the diagnostic process is NOT a root cause" in policy
    assert "Treat a failed diagnostic action as missing/unavailable evidence" in policy


def test_finalization_policy_keeps_peer_request_specific_and_generalized() -> None:
    policy = _normalized(SpecialistReActExecutor.FINALIZATION_POLICY)

    assert "Static RAG knowledge alone" in policy
    assert "SPECIFIC abnormal causal mechanism" in policy
    assert "Normal-state facts" in policy
    assert "never root causes" in policy
    assert "Choose assistance_domain from the unresolved hypothesis" in policy
    assert "NOT from the original detector name" in policy
    assert "specific evidence the peer should collect" in policy
    assert "NEVER output confirmed or probable" in policy
    assert "Never use probable to mean" in policy
    assert "never remediation" in policy
    assert "step limit" in policy
    assert "duplicate-action saturation is NOT evidence" in policy
    assert "MUST NOT by itself upgrade inconclusive to probable" in policy
    assert "bounded inconclusive is a valid result" in policy
    assert "Tool execution/validation failures are investigation metadata" in policy
    assert "MUST NOT appear as root_cause" in policy


def test_technical_lead_rejects_diagnostic_process_failures_as_root_causes() -> None:
    policy = _normalized(TechnicalLeadReviewReasoner.SYSTEM_PROMPT)

    assert "diagnostic-process failure is NOT a root cause" in policy
    assert "Invalid tool arguments" in policy
    assert "do not preserve it as a causal diagnosis" in policy
    assert "Unconfirmed causal mechanism after bounded autonomous investigation" in policy
