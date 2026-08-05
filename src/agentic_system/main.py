from __future__ import annotations

import argparse
import asyncio
import logging

from .agents.coordinator import IncidentCoordinatorAgent
from .agents.critic import CriticAgent
from .agents.diagnostic_executor import DiagnosticExecutorAgent
from .agents.evidence import LogsAgent, MetricsAgent
from .agents.hypothesis import HypothesisAgent
from .agents.investigation import InvestigationAgent
from .agents.topology import TopologyAgent
from .config import load_settings
from .detectors.manager import DetectorManager
from .diagnostics import DiagnosticHandlers
from .models import Incident
from .opensearch.client import OpenSearchClient
from .opensearch.repositories import LogsRepository, MetricsRepository
from .topology import TopologyRegistry
from .workspace import IncidentWorkspace


async def run(config_path: str, demo: bool = False) -> None:
    settings = load_settings(config_path)
    workspace = IncidentWorkspace()
    client = OpenSearchClient(settings.opensearch.base_url, settings.opensearch.username,
                              settings.opensearch.password, settings.opensearch.verify_ssl)
    metrics_repo = MetricsRepository(client)
    logs_repo = LogsRepository(client)

    if settings.sync_detectors_on_start:
        await DetectorManager(client, metrics_repo).synchronise()

    topology_registry = TopologyRegistry.from_yaml(settings.topology_file)
    diagnostic_handlers = DiagnosticHandlers(metrics_repo, logs_repo)

    coordinator = IncidentCoordinatorAgent(settings.coordinator.jid, settings.coordinator.password, workspace)
    metrics = MetricsAgent(settings.metrics.jid, settings.metrics.password, workspace)
    logs = LogsAgent(settings.logs.jid, settings.logs.password, workspace)
    topology = TopologyAgent(settings.topology.jid, settings.topology.password, workspace)
    hypothesis = HypothesisAgent(settings.hypothesis.jid, settings.hypothesis.password, workspace)
    investigation = InvestigationAgent(settings.investigation.jid, settings.investigation.password, workspace)
    diagnostic = DiagnosticExecutorAgent(settings.diagnostic_executor.jid, settings.diagnostic_executor.password, workspace)
    critic = CriticAgent(settings.critic.jid, settings.critic.password, workspace)

    coordinator.evidence_agents = [settings.metrics.jid, settings.logs.jid, settings.topology.jid]
    coordinator.hypothesis_agent = settings.hypothesis.jid
    coordinator.investigation_agent = settings.investigation.jid
    coordinator.critic_agent = settings.critic.jid
    coordinator.diagnostic_executor_agent = settings.diagnostic_executor.jid

    for agent in (metrics, logs, topology, hypothesis, investigation, diagnostic, critic):
        agent.coordinator_jid = settings.coordinator.jid
    investigation.diagnostic_executor_jid = settings.diagnostic_executor.jid
    metrics.metrics_repository = metrics_repo
    logs.logs_repository = logs_repo
    topology.topology_registry = topology_registry
    diagnostic.handlers = diagnostic_handlers.as_mapping()

    agents = [coordinator, metrics, logs, topology, hypothesis, investigation, diagnostic, critic]
    for agent in agents:
        await agent.start(auto_register=True)

    if demo:
        await coordinator.open_incident(Incident(detector_id='demo-cpu-detector', host_id='machine-03',
                                                  metric_name='cpu.usage_active', anomaly_score=0.91))

    try:
        while coordinator.is_alive():
            await asyncio.sleep(1)
    finally:
        for agent in reversed(agents):
            await agent.stop()
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='src/agentic_system/config/agents.yaml')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.config, args.demo))


if __name__ == '__main__':
    main()
