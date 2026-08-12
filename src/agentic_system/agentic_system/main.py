from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import signal
import threading
from typing import Any

from .config import RuntimeConfig, load_runtime_config


LOGGER = logging.getLogger("agentic_system")
STOP_EVENT = threading.Event()
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

        payload = json.dumps(HEALTH_STATE).encode("utf-8")
        self.send_response(200 if HEALTH_STATE.get("status") == "ok" else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.debug("health: " + format, *args)


def _install_signal_handlers() -> None:
    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %s; stopping agentic backend", signum)
        STOP_EVENT.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    _install_signal_handlers()

    config = load_runtime_config()
    _log_runtime(config)

    HEALTH_STATE.update(
        {
            "status": "ok",
            "phase": "infrastructure-ready",
            "agents_configured": len(config.agents),
            "roles": [agent.role for agent in config.agents],
            "xmpp_domain": config.xmpp_domain,
            "mcp_url": config.mcp_url,
            "reasoning_model": config.reasoning_model,
            "tool_model": config.tool_model,
        }
    )

    server = ThreadingHTTPServer((config.health_host, config.health_port), HealthHandler)
    server.timeout = 1.0
    LOGGER.info(
        "Health endpoint listening on http://%s:%d/health",
        config.health_host,
        config.health_port,
    )
    LOGGER.info(
        "Infrastructure bootstrap is ready. SPADE/BDI/ReAct agent execution "
        "will be attached to this runtime in the next implementation step."
    )

    try:
        while not STOP_EVENT.is_set():
            server.handle_request()
    finally:
        HEALTH_STATE["status"] = "stopping"
        server.server_close()
        LOGGER.info("Agentic backend stopped")


if __name__ == "__main__":
    main()
