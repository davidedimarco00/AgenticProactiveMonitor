from agentic_system.agents import (
    ApplicationEngineerAgent,
    NetworkEngineerAgent,
    SoftwareDeveloperAgent,
    SpecialistAgent,
    SystemEngineerAgent,
    TechnicalLeadAgent,
)
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
    assert TechnicalLeadAgent.__module__ == "agentic_system.agents.technical_lead"
    assert SpecialistAgent.__module__ == "agentic_system.agents.specialist"
    assert SystemEngineerAgent is SpecialistAgent
    assert NetworkEngineerAgent is SpecialistAgent
    assert ApplicationEngineerAgent is SpecialistAgent
    assert SoftwareDeveloperAgent is SpecialistAgent
    assert RoleLLMProvider.__module__ == "agentic_system.reasoning.models"
    assert SpecialistReActExecutor.__module__ == (
        "agentic_system.reasoning.langchain_agent"
    )
    assert build_incident_report.__module__ == "agentic_system.incidents.reporting"
