from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import signal
import threading
from typing import Any

import spade

from .config import RuntimeConfig, load_runtime_config
from .runtime import AgentRuntime


LOGGER = logging.getLogger("agentic_system")
HEALTH_LOCK = threading.Lock()
HEALTH_STATE: dict[str, Any] = {
    "status": "starting",
    "component": "agentic-backend",
    "phase": "bootstrap",
}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
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
    LOGGER.info("Configured logical agents: %d", len(config.agents))
    for agent in config.agents:
        LOGGER.info("  - %s [%s] jid=%s", agent.display_name, agent.role, agent.jid)

    LOGGER.info("XMPP endpoint: %s:%d", config.xmpp_host, config.xmpp_port)
    LOGGER.info("MCP endpoint: %s", config.mcp_url)
    LOGGER.info("Ollama endpoint: %s", config.ollama_url)
    LOGGER.info("Reasoning model: %s", config.reasoning_model)
    LOGGER.info("Tool model: %s", config.tool_model)
    LOGGER.info("LLM context: %d", config.llm_context)
    LOGGER.info("Max concurrent LLM calls: %d", config.max_llm_concurrency)


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


async def _run_backend() -> None:
    config = load_runtime_config()
    _log_runtime(config)

    health_server, health_thread = _start_health_server(config)
    runtime = AgentRuntime(config)
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    _set_health(
        status="starting",
        phase="starting-agents",
        agents_configured=len(config.agents),
        agents_running=0,
        roles=[agent.role for agent in config.agents],
        xmpp_domain=config.xmpp_domain,
        mcp_url=config.mcp_url,
        reasoning_model=config.reasoning_model,
        tool_model=config.tool_model,
    )

    try:
        await runtime.start()

        _set_health(
            status="ok",
            phase="agents-running",
            agents_running=runtime.running_count,
            agents=runtime.snapshot(),
        )
        LOGGER.info("Agentic backend is ready with five active SPADE agents")

        while not stop_event.is_set():
            _set_health(
                agents_running=runtime.running_count,
                agents=runtime.snapshot(),
            )
            await asyncio.sleep(1)
    finally:
        _set_health(status="stopping", phase="stopping-agents")
        await runtime.stop()
        _set_health(
            agents_running=0,
            agents=runtime.snapshot(),
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
