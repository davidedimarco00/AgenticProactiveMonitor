import pytest

from agentic_system.agents.peer_help import PeerHelpReasoner
from agentic_system.reasoning.specialist_react import SpecialistReActExecutor
from agentic_system.reasoning.langchain_agent import ReActInvestigationResult


def _parse(raw: str, *, own_domain: str = "system"):
    return PeerHelpReasoner._parse_response(raw, own_domain=own_domain)


def test_peer_help_parser_accepts_a_cross_domain_request() -> None:
    decision = _parse(
        """{
          "needs_help": true,
          "target_domain": "network",
          "reason": "Latency between the two services should be checked from the network path."
        }"""
    )
    assert decision.needs_help is True
    assert decision.target_domain == "network"
    assert "network" in decision.reason


def test_peer_help_parser_accepts_no_help_needed() -> None:
    decision = _parse('{"needs_help": false, "target_domain": null, "reason": ""}')
    assert decision.needs_help is False
    assert decision.target_domain is None


def test_peer_help_parser_rejects_own_domain_target() -> None:
    with pytest.raises(RuntimeError, match="own domain"):
        _parse(
            '{"needs_help": true, "target_domain": "system", "reason": "stay in my lane"}',
            own_domain="system",
        )


def test_peer_help_parser_rejects_diagnostic_content() -> None:
    with pytest.raises(RuntimeError, match="diagnostic content"):
        _parse(
            """{
              "needs_help": true,
              "target_domain": "network",
              "reason": "check the path",
              "root_cause": "packet loss on the uplink"
            }"""
        )


def test_peer_help_parser_requires_reason_when_help_needed() -> None:
    with pytest.raises(RuntimeError, match="requires a reason"):
        _parse('{"needs_help": true, "target_domain": "application", "reason": ""}')


def _result(**overrides) -> ReActInvestigationResult:
    base = dict(
        task_id="TASK-1",
        incident_id="INC-1",
        agent_role="system_engineer",
        summary="System resources look elevated but the trigger is unclear.",
        diagnosis_status="inconclusive",
        root_cause=None,
        causal_chain=(),
        confidence=0.5,
        findings=("CPU above baseline.",),
        evidence=({"tool": "get_runtime_stats"},),
        hypotheses=("Workload spike.",),
        recommended_next_steps=("Recheck after workload drops.",),
        assistance_required=True,
        assistance_domain="application",
        react_steps=4,
        tools_used=("get_runtime_stats",),
        conversation_id="react:system_engineer:INC-1:TASK-1",
    )
    base.update(overrides)
    return ReActInvestigationResult(**base)


def test_finalize_with_peer_help_merges_and_stops_asking_for_help() -> None:
    cached = _result()
    peer_payload = {
        "diagnosis_status": "probable",
        "summary": "The application service was retrying failed calls, driving CPU.",
        "root_cause": "Retry storm in the application service.",
        "causal_chain": ["Downstream errors", "Client retries", "CPU rises"],
        "confidence": 0.72,
        "findings": ["Error rate spiked before the CPU anomaly."],
        "hypotheses": ["Retry storm."],
        "recommended_next_steps": ["Cap client retries."],
        "evidence": [{"tool": "get_logs"}],
    }

    combined = SpecialistReActExecutor.finalize_with_peer_help(
        cached_result=cached,
        peer_result=peer_payload,
        peer_role="application_engineer",
        reason="Correlate the CPU rise with application error/retry behaviour.",
    )

    assert combined.assistance_required is False
    assert combined.assistance_domain is None
    assert combined.diagnosis_status == "probable"
    assert combined.root_cause == "Retry storm in the application service."
    assert "Error rate spiked before the CPU anomaly." in combined.findings
    assert "CPU above baseline." in combined.findings
    assert combined.confidence == pytest.approx(0.72)
    assert len(combined.evidence) == 2
    assert combined.peer_consultation["target_role"] == "application_engineer"
    assert combined.peer_consultation["status"] == "completed"
    assert combined.peer_consultation["peer_findings_count"] == 1


def test_finalize_keeps_primary_root_cause_when_present() -> None:
    cached = _result(
        diagnosis_status="probable",
        root_cause="Primary confirmed mechanism.",
        causal_chain=("A", "B"),
        assistance_required=True,
        assistance_domain="network",
    )
    combined = SpecialistReActExecutor.finalize_with_peer_help(
        cached_result=cached,
        peer_result={"diagnosis_status": "inconclusive", "confidence": 0.3, "summary": ""},
        peer_role="network_engineer",
        reason="second opinion",
    )
    assert combined.root_cause == "Primary confirmed mechanism."
    assert combined.diagnosis_status == "probable"
    assert combined.assistance_required is False
