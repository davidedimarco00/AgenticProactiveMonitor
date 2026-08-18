from pathlib import Path

import pytest

from agentic_system.agents.technical_lead.triage import TechnicalLeadTriageReasoner


def test_triage_parser_accepts_only_coordination_decision() -> None:
    assessment = TechnicalLeadTriageReasoner._parse_response(
        """{
          "probable_domain": "system",
          "recommended_agent": "system_engineer",
          "confidence": 0.88,
          "rationale": "The detector metadata points first to host and container resources."
        }"""
    )

    assert assessment.probable_domain == "system"
    assert assessment.recommended_agent == "system_engineer"
    assert assessment.confidence == 0.88
    assert "root cause" not in assessment.rationale.lower()


def test_triage_parser_rejects_diagnostic_output() -> None:
    with pytest.raises(RuntimeError, match="diagnostic content"):
        TechnicalLeadTriageReasoner._parse_response(
            """{
              "probable_domain": "system",
              "recommended_agent": "system_engineer",
              "confidence": 0.95,
              "rationale": "Investigate system resources first.",
              "root_cause": "CPU saturation caused by workload"
            }"""
        )


def test_technical_lead_agentspeak_plan_manages_but_does_not_diagnose() -> None:
    asl_path = (
        Path(__file__).parents[2]
        / "agentic_system"
        / "bdi"
        / "jason"
        / "agents"
        / "technical_lead.asl"
    )
    source = asl_path.read_text(encoding="utf-8")
    executable_source = "\n".join(
        line.split("//", 1)[0]
        for line in source.splitlines()
    ).lower()

    assert "!manage_incident" in executable_source
    assert "!triage_incident" in executable_source
    assert "request_triage_analysis" in executable_source
    assert "!select_primary_investigator" in executable_source
    assert "commit_primary_investigator" in executable_source
    assert "diagnose" not in executable_source
    assert "root_cause" not in executable_source
    assert "remediation" not in executable_source
