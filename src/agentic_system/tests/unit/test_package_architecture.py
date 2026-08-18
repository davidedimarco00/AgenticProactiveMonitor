from agentic_system.agents.application_engineer import ApplicationEngineerAgent
from agentic_system.agents.network_engineer import NetworkEngineerAgent
from agentic_system.agents.roles import (
    ApplicationEngineerAgent as LegacyApplicationEngineerAgent,
)
from agentic_system.agents.roles import NetworkEngineerAgent as LegacyNetworkEngineerAgent
from agentic_system.agents.roles import SoftwareDeveloperAgent as LegacySoftwareDeveloperAgent
from agentic_system.agents.roles import SystemEngineerAgent as LegacySystemEngineerAgent
from agentic_system.agents.roles import TechnicalLeadAgent as LegacyTechnicalLeadAgent
from agentic_system.agents.software_developer import SoftwareDeveloperAgent
from agentic_system.agents.system_engineer import SystemEngineerAgent
from agentic_system.agents.technical_lead import TechnicalLeadAgent
from agentic_system.ai.providers import HybridLLMProvider
from agentic_system.anomaly_watcher import AnomalyObservation as LegacyAnomalyObservation
from agentic_system.config import RuntimeConfig
from agentic_system.domain.anomalies import AnomalyObservation
from agentic_system.incident_repository import IncidentRepository as LegacyIncidentRepository
from agentic_system.infrastructure.mongodb import IncidentRepository
from agentic_system.infrastructure.opensearch import OpenSearchAnomalyWatcher
from agentic_system.infrastructure.reports import build_incident_report
from agentic_system.providers import HybridLLMProvider as LegacyHybridLLMProvider
from agentic_system.reports import build_incident_report as legacy_build_incident_report
from agentic_system.runtime import AgentRuntime


def test_responsibility_packages_are_the_canonical_implementations() -> None:
    assert LegacyAnomalyObservation is AnomalyObservation
    assert LegacyIncidentRepository is IncidentRepository
    assert LegacyHybridLLMProvider is HybridLLMProvider
    assert legacy_build_incident_report is build_incident_report

    assert LegacyTechnicalLeadAgent is TechnicalLeadAgent
    assert LegacySystemEngineerAgent is SystemEngineerAgent
    assert LegacyNetworkEngineerAgent is NetworkEngineerAgent
    assert LegacyApplicationEngineerAgent is ApplicationEngineerAgent
    assert LegacySoftwareDeveloperAgent is SoftwareDeveloperAgent

    assert RuntimeConfig.__module__ == "agentic_system.config.settings"
    assert AgentRuntime.__module__ == "agentic_system.runtime.agent_runtime"
    assert AnomalyObservation.__module__ == "agentic_system.domain.anomalies.models"
    assert OpenSearchAnomalyWatcher.__module__ == (
        "agentic_system.infrastructure.opensearch.anomaly_watcher"
    )
    assert IncidentRepository.__module__ == (
        "agentic_system.infrastructure.mongodb.incident_repository"
    )
    assert TechnicalLeadAgent.__module__ == (
        "agentic_system.agents.technical_lead.agent"
    )
