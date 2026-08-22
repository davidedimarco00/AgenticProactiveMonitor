import pytest

from agentic_system.agents.prompts import SPECIALIST_ROLE_PROFILES, specialist_system_prompt
from agentic_system.reasoning import SpecialistReActExecutor


@pytest.mark.parametrize("role", sorted(SPECIALIST_ROLE_PROFILES))
def test_specialist_prompt_is_role_specific_and_evidence_first(role: str) -> None:
    prompt = specialist_system_prompt(role)
    profile = SPECIALIST_ROLE_PROFILES[role]

    assert profile["display_name"] in prompt
    assert profile["priority"] in prompt
    assert profile["boundary"] in prompt
    assert "live MCP observations" in prompt
    assert "RAG knowledge alone is not evidence" in prompt
    assert "specific missing evidence" in prompt


def test_specialist_prompt_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="Unsupported specialist role"):
        specialist_system_prompt("database_engineer")


def test_tool_selection_policy_separates_live_evidence_from_rag() -> None:
    policy = SpecialistReActExecutor.TOOL_SELECTION_POLICY

    assert "exactly ONE" in policy
    assert "Live runtime claims" in policy
    assert "RAG/project knowledge" in policy
    assert "cannot prove" in policy
    assert "Do not repeat" in policy
    assert "Do not diagnose" in policy


def test_finalization_policy_keeps_peer_request_specific() -> None:
    policy = SpecialistReActExecutor.FINALIZATION_POLICY

    assert "Static RAG knowledge alone cannot confirm" in policy
    assert "specific evidence the peer should collect" in policy
    assert "never remediation" in policy
