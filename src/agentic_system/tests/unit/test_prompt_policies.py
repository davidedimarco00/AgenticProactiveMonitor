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


def test_reasoning_policy_distinguishes_symptom_from_root_cause() -> None:
    policy = _normalized(SpecialistReActExecutor.REASONING_POLICY)

    assert "is NOT by itself a root cause" in policy
    assert "current_hypothesis must name the causal process" in policy
    assert "If you can only restate the anomaly, gather more evidence instead" in policy


def test_finalization_policy_keeps_peer_request_specific() -> None:
    policy = _normalized(SpecialistReActExecutor.FINALIZATION_POLICY)

    assert "Static RAG knowledge alone cannot confirm" in policy
    assert "specific evidence the peer should collect" in policy
    assert "NEVER output confirmed or probable with root_cause=null" in policy
    assert "Never use probable to mean \"the cause is unknown\"" in policy
    assert "never remediation" in policy
