import pytest

from agentic_system.agents.prompts import SPECIALIST_ROLE_PROFILES, specialist_system_prompt
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
    assert "Never guess a common/default port or endpoint" in policy
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
