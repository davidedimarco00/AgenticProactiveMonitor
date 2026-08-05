from __future__ import annotations

import asyncio

from ..config import load_settings
from ..opensearch.client import OpenSearchClient
from ..opensearch.repositories import LogsRepository, MetricsRepository
from ..topology import TopologyRegistry
from .agents import CoordinatorAgent, CriticAgent, EvidenceAgent, ReasoningAgent, RemediationAgent
from .models import IncidentContext
from .ollama import OllamaClient
from .services import CriticService, EvidenceService, ReasoningService, RemediationService
from .workspace import Workspace


async def run(config_path: str) -> None:
    settings = load_settings(config_path)
    workspace = Workspace()
    opensearch = OpenSearchClient(
        settings.opensearch.base_url,
        settings.opensearch.username,
        settings.opensearch.password,
        settings.opensearch.verify_ssl,
    )
    ollama = OllamaClient(
        settings.ollama.base_url,
        settings.ollama.model,
        temperature=settings.ollama.temperature,
        timeout_seconds=settings.ollama.timeout_seconds,
        keep_alive=settings.ollama.keep_alive,
        max_retries=settings.ollama.max_retries,
    )
    if settings.ollama.check_model_on_start:
        await ollama.ensure_model_available()

    metrics_repo = MetricsRepository(opensearch)
    logs_repo = LogsRepository(opensearch)
    topology = TopologyRegistry.from_yaml(settings.topology_file)

    coordinator = CoordinatorAgent(settings.coordinator.jid, settings.coordinator.password, workspace)
    evidence = EvidenceAgent(settings.metrics.jid, settings.metrics.password, workspace)
    reasoning = ReasoningAgent(settings.hypothesis.jid, settings.hypothesis.password, workspace)
    critic = CriticAgent(settings.critic.jid, settings.critic.password, workspace)
    remediation = RemediationAgent(
        settings.diagnostic_executor.jid,
        settings.diagnostic_executor.password,
        workspace,
    )

    coordinator.evidence_jid = settings.metrics.jid
    coordinator.reasoning_jid = settings.hypothesis.jid
    coordinator.critic_jid = settings.critic.jid
    coordinator.remediation_jid = settings.diagnostic_executor.jid
    evidence.coordinator_jid = settings.coordinator.jid
    evidence.service = EvidenceService(metrics_repo, logs_repo, topology)
    reasoning.coordinator_jid = settings.coordinator.jid
    reasoning.service = ReasoningService(ollama)
    critic.coordinator_jid = settings.coordinator.jid
    critic.service = CriticService(ollama)
    remediation.coordinator_jid = settings.coordinator.jid
    remediation.service = RemediationService()

    agents = [coordinator, evidence, reasoning, critic, remediation]
    try:
        for agent in agents:
            await agent.start(auto_register=True)

        incident = IncidentContext(
            detector_id="demo",
            host_id="machine-03",
            metric_name="cpu.usage_active",
            anomaly_score=0.91,
        )
        await coordinator.open(incident)

        while coordinator.is_alive():
            await asyncio.sleep(1)
    finally:
        for agent in reversed(agents):
            if agent.is_alive():
                await agent.stop()
        await ollama.close()
        await opensearch.close()
