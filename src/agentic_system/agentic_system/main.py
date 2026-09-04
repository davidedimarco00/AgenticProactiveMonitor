from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import signal
import threading
from typing import Any, Awaitable, Callable

import spade
import uvicorn

from .api import attach_anomaly_inbox_api, attach_test_support_api, create_api_app
from .incidents import (
    AgentTaskWorkflow,
    AnomalyObservation,
    IncidentCoordinator,
    IncidentCorrelationPolicy,
    IncidentWorkflow,
    IncidentWorkflowResult,
    ReActIncidentCoordinator,
)
from .integrations import IncidentRepository, MongoAnomalyInbox, OpenSearchDetectorCatalog
from .runtime import AgentRuntime
from .settings import RuntimeConfig, load_runtime_config


LOGGER = logging.getLogger("agentic_system")
HEALTH_LOCK = threading.Lock()
HEALTH_STATE: dict[str, Any] = {
    "status": "starting",
    "component": "agentic-backend",
    "phase": "bootstrap",
}
AUTONOMOUS_TERMINAL_STATUSES = {"RESOLVED", "CLOSED", "OPERATOR_ACTION_REQUIRED"}
ANOMALY_INBOX_RECOVERY_INDEX = "agentic-anomaly-inbox-recovery"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/health", "/ready"}:
            self.send_response(404)
            self.end_headers()
            return

        with HEALTH_LOCK:
            snapshot = dict(HEALTH_STATE)

        payload = json.dumps(snapshot).encode("utf-8")
        self.send_response(200 if snapshot.get("status") == "ok" else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.debug("health: " + format, *args)


def _set_health(**values: Any) -> None:
    with HEALTH_LOCK:
        HEALTH_STATE.update(values)


def _log_runtime(config: RuntimeConfig) -> None:
    LOGGER.info("Agentic backend container initialized")
    LOGGER.info("Configured SPADE-LLM agents: %d", len(config.agents))
    for agent in config.agents:
        LOGGER.info("  - %s [%s] jid=%s", agent.display_name, agent.role, agent.jid)

    LOGGER.info("XMPP endpoint: %s:%d", config.xmpp_host, config.xmpp_port)
    LOGGER.info("MCP endpoint: %s", config.mcp_url)
    LOGGER.info("OpenSearch endpoint: %s", config.opensearch_url)
    LOGGER.info(
        "Anomaly watcher: enabled=%s poll=%.1fs lookback=%ss",
        config.enable_opensearch_anomaly_watcher,
        config.anomaly_watch_poll_seconds,
        config.anomaly_watch_lookback_seconds,
    )
    LOGGER.info(
        "Incident correlation window: %ss",
        config.incident_correlation_window_seconds,
    )
    LOGGER.info(
        "AgentSpeak BDI: TechnicalLeadASL=%s action_timeout=%.1fs concurrency=%d",
        config.agentspeak_technical_lead_asl,
        config.agentspeak_action_timeout_seconds,
        config.agentspeak_bdi_max_concurrency,
    )
    LOGGER.info("Ollama endpoint: %s", config.ollama_url)
    LOGGER.info("Reasoning model: ollama/%s", config.reasoning_model)
    LOGGER.info("Tool-calling model: ollama/%s", config.tool_model)
    LOGGER.info("Embedding model: ollama/%s", config.embedding_model)
    LOGGER.info("SPADE-LLM interaction memory: %s", config.spade_llm_memory_path)
    LOGGER.info("MongoDB database: %s", config.mongodb_database)
    LOGGER.info("REST API: http://%s:%d (Swagger: /docs)", config.api_host, config.api_port)
    LOGGER.info("Synthetic anomaly test hooks enabled: %s", config.enable_test_anomaly_injection)


def _start_health_server(config: RuntimeConfig) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer((config.health_host, config.health_port), HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="agentic-health-server",
        daemon=True,
    )
    thread.start()
    LOGGER.info(
        "Health endpoint listening on http://%s:%d/health",
        config.health_host,
        config.health_port,
    )
    return server, thread


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(
                signum,
                lambda _signum, _frame: loop.call_soon_threadsafe(stop_event.set),
            )


async def _wait_for_api_server(
    api_server: uvicorn.Server,
    api_task: asyncio.Task[None],
    *,
    timeout_seconds: float = 10.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not api_server.started:
        if api_task.done():
            exc = api_task.exception()
            raise RuntimeError("FastAPI server stopped during startup") from exc
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("FastAPI server did not become ready in time")
        await asyncio.sleep(0.05)


def _build_durable_anomaly_handler(
    coordinator: IncidentCoordinator,
    anomaly_inbox: MongoAnomalyInbox,
) -> Callable[[AnomalyObservation], Awaitable[object]]:
    """Wrap incident coordination with the durable anomaly-inbox lifecycle."""

    async def handle(observation: AnomalyObservation) -> IncidentWorkflowResult:
        recovery_incident_id = observation.recovery_incident_id
        if recovery_incident_id:
            await anomaly_inbox.mark_incident_anomalies_processing(recovery_incident_id)

        result = await coordinator.handle_anomaly(observation)
        incident_id = str(result.incident["incident_id"])

        if not recovery_incident_id:
            linked = await anomaly_inbox.link_anomaly_to_incident(
                observation.deduplication_key,
                incident_id,
            )
            if linked is None:
                raise RuntimeError(
                    "Durable anomaly disappeared before incident linkage: "
                    f"{observation.deduplication_key}"
                )

        terminal_incident = await coordinator.wait_until_workflow_terminal(incident_id)

        if recovery_incident_id:
            await anomaly_inbox.mark_incident_anomalies_completed(incident_id)

        return IncidentWorkflowResult(
            incident=terminal_incident,
            created=result.created,
            correlated=result.correlated,
        )

    return handle


def _recovery_observation_from_inbox(
    record: dict[str, Any],
    incident_id: str,
) -> AnomalyObservation:
    return AnomalyObservation(
        result_id=f"recover:{incident_id}",
        result_index=ANOMALY_INBOX_RECOVERY_INDEX,
        detector_id=str(record.get("detector_id") or "").strip(),
        anomaly_grade=float(record.get("anomaly_grade") or 0.0),
        confidence=float(record.get("confidence") or 0.0),
        anomaly_score=(
            float(record["anomaly_score"])
            if record.get("anomaly_score") is not None
            else None
        ),
        data_start_time=None,
        data_end_time=None,
        execution_start_time=None,
        execution_end_time=None,
        recovery_incident_id=incident_id,
    )


async def _run_backend() -> None:
    config = load_runtime_config()
    _log_runtime(config)

    health_server, health_thread = _start_health_server(config)
    repository = IncidentRepository(config.mongodb_uri, config.mongodb_database)
    anomaly_inbox = MongoAnomalyInbox(config.mongodb_uri, config.mongodb_database)
    detector_context = OpenSearchDetectorCatalog(config.opensearch_url)
    correlation_policy = IncidentCorrelationPolicy(
        window_seconds=config.incident_correlation_window_seconds
    )
    incident_workflow = IncidentWorkflow(repository, correlation_policy)
    task_workflow = AgentTaskWorkflow(repository)
    runtime = AgentRuntime(config)
    runtime.anomaly_intake.anomaly_inbox = anomaly_inbox
    incident_coordinator = ReActIncidentCoordinator(
        incident_workflow,
        runtime,
        detector_context,
        task_workflow,
        repository,
    )

    runtime.configure_anomaly_handler(
        _build_durable_anomaly_handler(incident_coordinator, anomaly_inbox)
    )

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    api_server: uvicorn.Server | None = None
    api_task: asyncio.Task[None] | None = None
    task_recovery: dict[str, int] = {"scanned": 0, "retrying": 0, "failed": 0}
    incident_recovery: dict[str, int] = {"scanned": 0, "resumed": 0, "failed": 0}
    anomaly_inbox_recovery: dict[str, int] = {
        "interrupted": 0,
        "reset_to_waiting": 0,
        "waiting": 0,
        "seeded_waiting": 0,
    }

    _set_health(
        status="starting",
        phase="connecting-mongodb",
        framework="SPADE-LLM",
        agents_configured=len(config.agents),
        agents_running=0,
        roles=[agent.role for agent in config.agents],
        xmpp_domain=config.xmpp_domain,
        mcp_url=config.mcp_url,
        opensearch_url=config.opensearch_url,
        provider_model=f"ollama/{config.reasoning_model}",
        reasoning_model=f"ollama/{config.reasoning_model}",
        tool_model=f"ollama/{config.tool_model}",
        embedding_model=f"ollama/{config.embedding_model}",
        interaction_memory_enabled=True,
        bdi_engine="Python AgentSpeak(L)",
        mongodb_database=config.mongodb_database,
        mongodb_reachable=False,
        api_port=config.api_port,
        team_communication_ok=False,
        unreachable_specialists=[],
        anomaly_watcher=runtime.anomaly_watch_snapshot(),
        anomaly_inbox_recovery=anomaly_inbox_recovery,
        task_recovery=task_recovery,
        incident_recovery=incident_recovery,
        test_anomaly_injection_enabled=config.enable_test_anomaly_injection,
        opensearch_anomaly_watcher_enabled=config.enable_opensearch_anomaly_watcher,
    )

    try:
        await repository.connect()
        await anomaly_inbox.connect()
        _set_health(mongodb_reachable=True, phase="recovering-anomaly-inbox")

        anomaly_inbox_recovery = await anomaly_inbox.recover_interrupted_processing()
        _set_health(
            anomaly_inbox_recovery=anomaly_inbox_recovery,
            phase="recovering-agent-tasks",
        )

        task_recovery = (await task_workflow.recover_incomplete_tasks()).to_dict()
        _set_health(task_recovery=task_recovery, phase="starting-agents")

        await runtime.start(start_observation_pipeline=False)

        _set_health(phase="seeding-incident-recovery")
        recovery_observations = await incident_coordinator.build_recovery_observations()
        recovery_incident_ids = {
            str(observation.recovery_incident_id)
            for observation in recovery_observations
            if observation.recovery_incident_id
        }

        waiting_records = await anomaly_inbox.list_anomalies(
            states=["WAITING"],
            limit=4096,
            ascending=True,
        )
        backlog_observations: list[AnomalyObservation] = []
        for record in waiting_records:
            incident_id = str(record.get("incident_id") or "").strip()
            if not incident_id:
                backlog_observations.append(AnomalyObservation.from_dict(record))
                continue

            incident = await repository.get_incident(incident_id)
            if incident is None:
                released = await anomaly_inbox.release_orphaned_incident_link(
                    str(record["anomaly_key"])
                )
                if released is not None:
                    backlog_observations.append(AnomalyObservation.from_dict(released))
                continue

            status = str(incident.get("status") or "").upper()
            if status in AUTONOMOUS_TERMINAL_STATUSES:
                await anomaly_inbox.mark_anomaly_completed(str(record["anomaly_key"]))
                continue

            if incident_id not in recovery_incident_ids:
                recovery_observations.append(
                    _recovery_observation_from_inbox(record, incident_id)
                )
                recovery_incident_ids.add(incident_id)

        for incident_id in recovery_incident_ids:
            await anomaly_inbox.mark_incident_anomalies_recovery_queued(incident_id)

        seeded_recovery = await runtime.enqueue_recovery_observations(recovery_observations)
        seeded_waiting = 0
        for observation in backlog_observations:
            if await runtime.anomaly_intake.enqueue(observation):
                seeded_waiting += 1

        incident_recovery = {
            "scanned": len(recovery_observations),
            "resumed": seeded_recovery,
            "failed": 0,
        }
        anomaly_inbox_recovery = {
            **anomaly_inbox_recovery,
            "seeded_waiting": seeded_waiting,
        }

        # In normal mode the watcher starts together with the exclusive intake.
        # The test compose override pre-stops the watcher before its task is
        # scheduled, so synthetic FIFO tests are not polluted by OpenSearch.
        if not config.enable_opensearch_anomaly_watcher:
            runtime.anomaly_watcher.stop()
        runtime.start_observation_pipeline()
        _set_health(
            incident_recovery=incident_recovery,
            anomaly_inbox_recovery=anomaly_inbox_recovery,
            phase="starting-api",
        )

        api = create_api_app(runtime, repository)
        attach_anomaly_inbox_api(api, anomaly_inbox)
        if config.enable_test_anomaly_injection:
            attach_test_support_api(
                api,
                runtime=runtime,
                repository=repository,
                task_workflow=task_workflow,
            )
            LOGGER.warning(
                "Development test hooks enabled under /internal/v1/test; do not enable in production"
            )

        api_server = uvicorn.Server(
            uvicorn.Config(
                api,
                host=config.api_host,
                port=config.api_port,
                log_level="info",
                access_log=False,
                loop="asyncio",
            )
        )
        api_task = asyncio.create_task(api_server.serve(), name="agentic-fastapi")
        await _wait_for_api_server(api_server, api_task)

        _set_health(
            status="ok",
            phase="agents-running",
            agents_running=runtime.running_count,
            agents=runtime.snapshot(),
            communication_probe=runtime.communication_probe,
            team_communication_ok=runtime.team_communication_ok,
            unreachable_specialists=runtime.unreachable_specialists,
            anomaly_watcher=runtime.anomaly_watch_snapshot(),
            anomaly_inbox_recovery=anomaly_inbox_recovery,
            task_recovery=task_recovery,
            incident_recovery=incident_recovery,
            api_docs=f"http://{config.api_host}:{config.api_port}/docs",
        )
        LOGGER.info("Agentic backend is ready with five active SPADE-LLM agents")

        while not stop_event.is_set():
            _set_health(
                agents_running=runtime.running_count,
                agents=runtime.snapshot(),
                communication_probe=runtime.communication_probe,
                team_communication_ok=runtime.team_communication_ok,
                unreachable_specialists=runtime.unreachable_specialists,
                anomaly_watcher=runtime.anomaly_watch_snapshot(),
                anomaly_inbox_recovery=anomaly_inbox_recovery,
                task_recovery=task_recovery,
                incident_recovery=incident_recovery,
            )
            if api_task.done():
                exc = api_task.exception()
                raise RuntimeError("FastAPI server stopped unexpectedly") from exc
            await asyncio.sleep(1)
    finally:
        _set_health(status="stopping", phase="stopping-api")
        if api_server is not None:
            api_server.should_exit = True
        if api_task is not None:
            try:
                await asyncio.wait_for(api_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                api_task.cancel()

        _set_health(phase="stopping-agents")
        await runtime.stop()
        await anomaly_inbox.close()
        await repository.close()
        _set_health(
            agents_running=0,
            agents=runtime.snapshot(),
            mongodb_reachable=False,
            team_communication_ok=runtime.team_communication_ok,
            unreachable_specialists=runtime.unreachable_specialists,
            anomaly_watcher=runtime.anomaly_watch_snapshot(),
        )
        health_server.shutdown()
        health_server.server_close()
        health_thread.join(timeout=5)
        LOGGER.info("Agentic backend stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    spade.run(_run_backend())


if __name__ == "__main__":
    main()
