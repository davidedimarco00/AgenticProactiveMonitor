from agentic_system.agents import (
    ApplicationEngineerAgent,
    NetworkEngineerAgent,
    SoftwareDeveloperAgent,
    SystemEngineerAgent,
    TechnicalLeadAgent,
)
from agentic_system.incidents import AnomalyObservation, build_incident_report
from agentic_system.integrations import IncidentRepository, OpenSearchAnomalyWatcher
from agentic_system.reasoning import HybridLLMProvider
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
    assert SystemEngineerAgent.__module__ == "agentic_system.agents.system_engineer"
    assert NetworkEngineerAgent.__module__ == "agentic_system.agents.network_engineer"
    assert ApplicationEngineerAgent.__module__ == (
        "agentic_system.agents.application_engineer"
    )
    assert SoftwareDeveloperAgent.__module__ == (
        "agentic_system.agents.software_developer"
    )
    assert HybridLLMProvider.__module__ == "agentic_system.reasoning.llm"
    assert build_incident_report.__module__ == "agentic_system.incidents.reporting"
