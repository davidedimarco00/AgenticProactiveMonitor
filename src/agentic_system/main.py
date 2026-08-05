from __future__ import annotations

import argparse
import asyncio
import logging

from .agents.coordinator import IncidentCoordinatorAgent
from .agents.critic import CriticAgent
from .agents.evidence import LogsAgent, MetricsAgent
from .agents.hypothesis import HypothesisAgent
from .config import load_settings
from .models import Incident
from .workspace import IncidentWorkspace


async def run(config_path: str) -> None:
    settings = load_settings(config_path)
    workspace = IncidentWorkspace()

    coordinator = IncidentCoordinatorAgent(settings.coordinator.jid, settings.coordinator.password, workspace)
    metrics = MetricsAgent(settings.metrics.jid, settings.metrics.password, workspace)
    logs = LogsAgent(settings.logs.jid, settings.logs.password, workspace)
    hypothesis = HypothesisAgent(settings.hypothesis.jid, settings.hypothesis.password, workspace)
    critic = CriticAgent(settings.critic.jid, settings.critic.password, workspace)

    coordinator.evidence_agents = [settings.metrics.jid, settings.logs.jid]
    coordinator.hypothesis_agent = settings.hypothesis.jid
    coordinator.critic_agent = settings.critic.jid
    metrics.coordinator_jid = settings.coordinator.jid
    logs.coordinator_jid = settings.coordinator.jid
    hypothesis.coordinator_jid = settings.coordinator.jid
    critic.coordinator_jid = settings.coordinator.jid

    agents = [coordinator, metrics, logs, hypothesis, critic]
    for agent in agents:
        await agent.start(auto_register=True)

    demo_incident = Incident(
        detector_id="demo-cpu-detector",
        host_id="machine-03",
        metric_name="cpu.usage_active",
        anomaly_score=0.91,
    )
    await coordinator.open_incident(demo_incident)

    try:
        while coordinator.is_alive():
            await asyncio.sleep(1)
    finally:
        for agent in reversed(agents):
            await agent.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="src/agentic_system/config/agents.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
