from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..config import load_settings
from ..detectors.manager import DetectorManager
from ..diagnostics.handlers import DiagnosticHandlers
from ..opensearch.client import OpenSearchClient
from ..opensearch.repositories import LogsRepository, MetricsRepository
from ..topology import TopologyRegistry
from .agents import CoordinatorAgent, CriticAgent, EvidenceAgent, ReasoningAgent, RemediationAgent
from .models import IncidentContext
from .ollama import OllamaClient
from .services import CriticService, EvidenceService, ReasoningService, RemediationService
from .workspace import Workspace

log = logging.getLogger(__name__)
_READY_FILE = Path("/tmp/agentic-system.ready")


async def run(config_path: str) -> None:
    settings = load_settings(config_path)
    workspace = Workspace()
    diagnostics: DiagnosticHandlers | None = None
    agents = []

    _READY_FILE.unlink(missing_ok=True)

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

    try:
        log.info("[1/7] Checking Ollama model %s", settings.ollama.model)
        if settings.ollama.check_model_on_start:
            await ollama.ensure_model_available()

        log.info("[2/7] Initialising OpenSearch repositories")
        metrics_repo = MetricsRepository(opensearch)
        logs_repo = LogsRepository(opensearch)

        if settings.sync_detectors_on_start:
            log.info("[3/7] Synchronising OpenSearch anomaly detectors")
            try:
                detectors = await DetectorManager(opensearch, metrics_repo).synchronise(
                    settings.detector_metrics
                )
                log.info("Detector registry contains %d managed detectors", len(detectors))
            except Exception as exc:
                log.warning(
                    "Detector synchronisation was skipped without stopping the MAS: %s",
                    exc,
                )
        else:
            log.info("[3/7] Detector synchronisation is managed by opensearch-init")

        log.info("[4/7] Loading topology from %s", settings.topology_file)
        topology = TopologyRegistry.from_yaml(settings.topology_file)

        try:
            diagnostics = DiagnosticHandlers(metrics_repo, logs_repo)
            log.info("Docker diagnostic tools are available")
        except Exception:
            diagnostics = None
            log.exception("Docker diagnostics are unavailable; OpenSearch checks remain active")

        log.info("[5/7] Creating the five SPADE agents")
        coordinator = CoordinatorAgent(
            settings.coordinator.jid,
            settings.coordinator.password,
            workspace,
        )
        evidence = EvidenceAgent(settings.evidence.jid, settings.evidence.password, workspace)
        reasoning = ReasoningAgent(settings.reasoning.jid, settings.reasoning.password, workspace)
        critic = CriticAgent(settings.critic.jid, settings.critic.password, workspace)
        remediation = RemediationAgent(
            settings.remediation.jid,
            settings.remediation.password,
            workspace,
        )

        coordinator.evidence_jid = settings.evidence.jid
        coordinator.reasoning_jid = settings.reasoning.jid
        coordinator.critic_jid = settings.critic.jid
        coordinator.remediation_jid = settings.remediation.jid

        evidence.coordinator_jid = settings.coordinator.jid
        evidence.service = EvidenceService(metrics_repo, logs_repo, topology, diagnostics)
        reasoning.coordinator_jid = settings.coordinator.jid
        reasoning.service = ReasoningService(ollama)
        critic.coordinator_jid = settings.coordinator.jid
        critic.service = CriticService(ollama)
        remediation.coordinator_jid = settings.coordinator.jid
        remediation.service = RemediationService()

        agents = [coordinator, evidence, reasoning, critic, remediation]

        log.info("[6/7] Connecting SPADE agents to the provisioned XMPP accounts")
        for agent in agents:
            await agent.start(auto_register=False)
            log.info("Started and authenticated agent %s", agent.jid)

        _READY_FILE.write_text("ready\n", encoding="utf-8")
        log.info("[7/7] Agentic system is ready")

        if settings.open_demo_incident:
            incident = IncidentContext(
                detector_id="demo",
                host_id="processing-service",
                metric_name="usage_active",
                anomaly_score=0.91,
            )
            log.info("Opening demo incident %s for %s", incident.incident_id, incident.host_id)
            await coordinator.open(incident)
        else:
            log.info("Demo incident disabled; waiting for perception events")

        while coordinator.is_alive():
            await asyncio.sleep(1)
    finally:
        _READY_FILE.unlink(missing_ok=True)
        for agent in reversed(agents):
            if agent.is_alive():
                await agent.stop()
        if diagnostics is not None:
            diagnostics.close()
        await ollama.close()
        await opensearch.close()
