from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import signal
import threading
from typing import Any

import spade
import uvicorn

from .api import create_api_app
from .incidents import (
    AgentTaskWorkflow,
    IncidentCoordinator,
    IncidentCorrelationPolicy,
    IncidentWorkflow,
)
from .integrations import IncidentRepository, OpenSearchDetectorCatalog
from .runtime import AgentRuntime
from .settings import RuntimeConfig, load_runtime_config


LOGGER = logging.getLogger("agentic_system")
HEALTH_LOCK = threading.Lock()
HEALTH_STATE: dict[str, Any] = {
    "status": "starting",
    "component": "agentic-backend",
    "phase": "bootstrap",
}


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
        "Anomaly watcher: poll=%.1fs lookback=%ss",
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


async def _run_backend() -> None:
    config = load_runtime_config()
    _log_runtime(config)

    health_server, health_thread = _start_health_server(config)
    repository = IncidentRepository(config.mongodb_uri, config.mongodb_database)
    detector_context = OpenSearchDetectorCatalog(config.opensearch_url)
    correlation_policy = IncidentCorrelationPolicy(
        window_seconds=config.incident_correlation_window_seconds
    )
    incident_workflow = IncidentWorkflow(repository, correlation_policy)
    task_workflow = AgentTaskWorkflow(repository)
    runtime = AgentRuntime(config)
    incident_coordinator = IncidentCoordinator(
        incident_workflow,
        runtime,
        detector_context,
        task_workflow,
        repository,
    )
    runtime.configure_anomaly_handler(incident_coordinator.handle_anomaly)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    api_server: uvicorn.Server | None = None
    api_task: asyncio.Task[None] | None = None
    task_recovery: dict[str, int] = {"scanned": 0, "retrying": 0, "failed": 0}
    incident_recovery: dict[str, int] = {"scanned": 0, "resumed": 0, "failed": 0}

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
        task_recovery=task_recovery,
        incident_recovery=incident_recovery,
    )

    try:
        await repository.connect()
        _set_health(mongodb_reachable=True, phase="recovering-agent-tasks")

        # Volatile DISPATCHED/RUNNING executions cannot survive a backend
        # restart. Persistently reclassify them before the agents start so the
        # next dispatcher can safely retry rather than duplicate work.
        task_recovery = (await task_workflow.recover_incomplete_tasks()).to_dict()
        _set_health(task_recovery=task_recovery, phase="starting-agents")

        await runtime.start()

        # Agents are now available, therefore durable incidents that previously
        # stopped in NEW/TAKEN_IN_CHARGE/TRIAGED can continue from their last
        # persisted state without replaying already completed stages.
        _set_health(phase="recovering-incidents")
        incident_recovery = await incident_coordinator.recover_incomplete_incidents()
        _set_health(incident_recovery=incident_recovery, phase="starting-api")

        api = create_api_app(runtime, repository)
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
