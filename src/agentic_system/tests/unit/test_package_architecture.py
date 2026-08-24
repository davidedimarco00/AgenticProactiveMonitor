from pathlib import Path

from agentic_system.agents import BaseAgent, SpecialistAgent, TechnicalLeadAgent
from agentic_system.incidents import AnomalyObservation, build_incident_report
from agentic_system.integrations import IncidentRepository, OpenSearchAnomalyWatcher
from agentic_system.reasoning import RoleLLMProvider, SpecialistReActExecutor
from agentic_system.runtime import AgentRuntime
from agentic_system.settings import RuntimeConfig


def test_feature_packages_are_the_canonical_implementations() -> None:
    assert RuntimeConfig.__module__ == "agentic_system.settings"
    assert AgentRuntime.__module__ == "agentic_system.runtime"
    assert AnomalyObservation.__module__ == "agentic_system.incidents.anomalies"
    assert OpenSearchAnomalyWatcher.__module__ == (
        "agentic_system.integrations.opensearch_watcher"
    )
    assert IncidentRepository.__module__ == "agentic_system.integrations.mongodb"
    assert BaseAgent.__module__ == "agentic_system.agents.base"
    assert TechnicalLeadAgent.__module__ == "agentic_system.agents.technical_lead"
    assert SpecialistAgent.__module__ == "agentic_system.agents.specialist"
    assert RoleLLMProvider.__module__ == "agentic_system.reasoning.models"
    assert SpecialistReActExecutor.__module__ == (
        "agentic_system.reasoning.context_robust_react"
    )
    assert build_incident_report.__module__ == "agentic_system.incidents.reporting"


def test_obsolete_wrapper_files_are_removed() -> None:
    package_root = Path(__file__).parents[2] / "agentic_system"
    agents = package_root / "agents"
    reasoning = package_root / "reasoning"

    for filename in (
        "roles.py",
        "system_engineer.py",
        "network_engineer.py",
        "application_engineer.py",
        "software_developer.py",
    ):
        assert not (agents / filename).exists()

    for filename in ("react.py", "llm.py", "review_bdi.py"):
        assert not (reasoning / filename).exists()

    assert not (reasoning / "plans" / "technical_lead_review.asl").exists()
