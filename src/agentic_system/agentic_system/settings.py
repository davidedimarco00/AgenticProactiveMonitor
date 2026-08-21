from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


EXPECTED_ROLES = (
    "technical_lead",
    "system_engineer",
    "network_engineer",
    "application_engineer",
    "software_developer",
)


@dataclass(frozen=True)
class AgentSpec:
    role: str
    display_name: str
    jid: str
    password_env: str
    password: str
    health_port: int


@dataclass(frozen=True)
class RuntimeConfig:
    agents: tuple[AgentSpec, ...]
    xmpp_domain: str
    xmpp_host: str
    xmpp_port: int
    xmpp_auto_register: bool
    mcp_url: str
    opensearch_url: str
    anomaly_watch_poll_seconds: float
    anomaly_watch_lookback_seconds: int
    ollama_url: str
    reasoning_model: str
    tool_model: str
    embedding_model: str
    spade_llm_memory_path: str
    health_host: str
    health_port: int
    api_host: str
    api_port: int
    mongodb_uri: str
    mongodb_database: str
    incident_correlation_window_seconds: int = 600
    agentspeak_technical_lead_asl: str = "/app/agentic_system/reasoning/plans/technical_lead.asl"
    agentspeak_specialist_asl: str = "/app/agentic_system/reasoning/plans/specialist.asl"
    agentspeak_action_timeout_seconds: float = 120.0
    agentspeak_bdi_max_concurrency: int = 2
    task_dispatch_timeout_seconds: float = 10.0
    max_llm_concurrency: int = 1
    react_max_steps: int = 10
    react_tool_timeout_seconds: float = 30.0
    # Synthetic anomaly injection is a first-class local/E2E test capability.
    # Production-like deployments can explicitly disable it with
    # ENABLE_TEST_ANOMALY_INJECTION=false, which removes the route entirely.
    enable_test_anomaly_injection: bool = True
    enable_opensearch_anomaly_watcher: bool = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Agent configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}

    if not isinstance(document, dict):
        raise RuntimeError("Agent configuration root must be a YAML mapping")

    return document


def load_runtime_config() -> RuntimeConfig:
    config_path = Path(os.getenv("AGENT_CONFIG_PATH", "/app/config/agents.yaml"))
    document = _load_yaml(config_path)

    runtime_section = document.get("runtime") or {}
    configured_agents = document.get("agents") or []

    if not isinstance(configured_agents, list):
        raise RuntimeError("'agents' must be a YAML list")

    expected_count = int(runtime_section.get("expected_agent_count", len(EXPECTED_ROLES)))
    if expected_count != len(EXPECTED_ROLES):
        raise RuntimeError(
            f"Runtime must declare exactly {len(EXPECTED_ROLES)} agents; "
            f"configured expected count is {expected_count}"
        )

    xmpp_domain = os.getenv("XMPP_DOMAIN", "xmpp").strip()
    if not xmpp_domain:
        raise RuntimeError("XMPP_DOMAIN cannot be empty")

    agents: list[AgentSpec] = []
    seen_roles: set[str] = set()
    seen_jids: set[str] = set()
    seen_health_ports: set[int] = set()

    for item in configured_agents:
        if not isinstance(item, dict):
            raise RuntimeError("Each agent entry must be a YAML mapping")

        role = str(item.get("role", "")).strip()
        display_name = str(item.get("display_name", "")).strip()
        jid_localpart = str(item.get("jid_localpart", "")).strip()
        password_env = str(item.get("password_env", "")).strip()
        health_port = int(item.get("health_port", 0))

        if not all((role, display_name, jid_localpart, password_env, health_port)):
            raise RuntimeError(f"Incomplete agent configuration: {item}")

        if role in seen_roles:
            raise RuntimeError(f"Duplicate agent role: {role}")

        jid = f"{jid_localpart}@{xmpp_domain}"
        if jid in seen_jids:
            raise RuntimeError(f"Duplicate agent JID: {jid}")
        if health_port in seen_health_ports:
            raise RuntimeError(f"Duplicate per-agent health port: {health_port}")

        password = os.getenv(password_env, "").strip()
        if not password:
            raise RuntimeError(
                f"Missing password for role '{role}'. Set environment variable {password_env}."
            )

        seen_roles.add(role)
        seen_jids.add(jid)
        seen_health_ports.add(health_port)
        agents.append(
            AgentSpec(
                role=role,
                display_name=display_name,
                jid=jid,
                password_env=password_env,
                password=password,
                health_port=health_port,
            )
        )

    if tuple(agent.role for agent in agents) != EXPECTED_ROLES:
        raise RuntimeError(
            "Agent roles/order must be exactly: " + ", ".join(EXPECTED_ROLES)
        )

    mcp_url = os.getenv("MCP_URL", "http://mcp-server:8000/mcp").strip()
    opensearch_url = os.getenv("OPENSEARCH_URL", "http://opensearch:9200").strip()
    anomaly_watch_poll_seconds = float(os.getenv("ANOMALY_WATCH_POLL_SECONDS", "5"))
    anomaly_watch_lookback_seconds = int(os.getenv("ANOMALY_WATCH_LOOKBACK_SECONDS", "300"))
    incident_correlation_window_seconds = int(
        os.getenv("INCIDENT_CORRELATION_WINDOW_SECONDS", "600")
    )
    agentspeak_technical_lead_asl = os.getenv(
        "AGENTSPEAK_TECHNICAL_LEAD_ASL",
        "/app/agentic_system/reasoning/plans/technical_lead.asl",
    ).strip()
    agentspeak_specialist_asl = os.getenv(
        "AGENTSPEAK_SPECIALIST_ASL",
        "/app/agentic_system/reasoning/plans/specialist.asl",
    ).strip()
    agentspeak_action_timeout_seconds = float(
        os.getenv("AGENTSPEAK_ACTION_TIMEOUT_SECONDS", "120")
    )
    agentspeak_bdi_max_concurrency = int(
        os.getenv("AGENTSPEAK_BDI_MAX_CONCURRENCY", "2")
    )
    task_dispatch_timeout_seconds = float(
        os.getenv("AGENT_TASK_DISPATCH_TIMEOUT_SECONDS", "10")
    )
    max_llm_concurrency = int(os.getenv("AGENT_MAX_LLM_CONCURRENCY", "1"))
    react_max_steps = int(os.getenv("AGENT_REACT_MAX_STEPS", "10"))
    react_tool_timeout_seconds = float(os.getenv("AGENT_REACT_TOOL_TIMEOUT_SECONDS", "30"))
    enable_test_anomaly_injection = _env_bool("ENABLE_TEST_ANOMALY_INJECTION", True)
    enable_opensearch_anomaly_watcher = _env_bool("ENABLE_OPENSEARCH_ANOMALY_WATCHER", True)
    ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").strip()
    reasoning_model = os.getenv("OLLAMA_REASONING_MODEL", "gemma4:e2b").strip()
    tool_model = os.getenv("OLLAMA_TOOL_MODEL", "qwen3.5:4b").strip()
    embedding_model = os.getenv(
        "OLLAMA_EMBEDDING_MODEL",
        "ibm/granite-embedding:30m",
    ).strip()
    spade_llm_memory_path = os.getenv(
        "SPADE_LLM_MEMORY_PATH",
        "/home/agentic/.spade_llm/memory",
    ).strip()
    mongodb_uri = os.getenv(
        "MONGODB_URI",
        "mongodb://agentic:change-this-local-password@mongodb:27017/agentic_monitor?authSource=admin",
    ).strip()
    mongodb_database = os.getenv("MONGODB_DATABASE", "agentic_monitor").strip()

    required_values = {
        "MCP_URL": mcp_url,
        "OPENSEARCH_URL": opensearch_url,
        "OLLAMA_URL": ollama_url,
        "OLLAMA_REASONING_MODEL": reasoning_model,
        "OLLAMA_TOOL_MODEL": tool_model,
        "OLLAMA_EMBEDDING_MODEL": embedding_model,
        "SPADE_LLM_MEMORY_PATH": spade_llm_memory_path,
        "MONGODB_URI": mongodb_uri,
        "MONGODB_DATABASE": mongodb_database,
        "AGENTSPEAK_TECHNICAL_LEAD_ASL": agentspeak_technical_lead_asl,
        "AGENTSPEAK_SPECIALIST_ASL": agentspeak_specialist_asl,
    }
    for name, value in required_values.items():
        if not value:
            raise RuntimeError(f"{name} cannot be empty")

    if anomaly_watch_poll_seconds <= 0:
        raise RuntimeError("ANOMALY_WATCH_POLL_SECONDS must be greater than zero")
    if anomaly_watch_lookback_seconds <= 0:
        raise RuntimeError("ANOMALY_WATCH_LOOKBACK_SECONDS must be greater than zero")
    if incident_correlation_window_seconds <= 0:
        raise RuntimeError("INCIDENT_CORRELATION_WINDOW_SECONDS must be greater than zero")
    if agentspeak_action_timeout_seconds <= 0:
        raise RuntimeError("AGENTSPEAK_ACTION_TIMEOUT_SECONDS must be greater than zero")
    if agentspeak_bdi_max_concurrency <= 0:
        raise RuntimeError("AGENTSPEAK_BDI_MAX_CONCURRENCY must be greater than zero")
    if task_dispatch_timeout_seconds <= 0:
        raise RuntimeError("AGENT_TASK_DISPATCH_TIMEOUT_SECONDS must be greater than zero")
    if max_llm_concurrency <= 0:
        raise RuntimeError("AGENT_MAX_LLM_CONCURRENCY must be greater than zero")
    if react_max_steps <= 0:
        raise RuntimeError("AGENT_REACT_MAX_STEPS must be greater than zero")
    if react_tool_timeout_seconds <= 0:
        raise RuntimeError("AGENT_REACT_TOOL_TIMEOUT_SECONDS must be greater than zero")

    return RuntimeConfig(
        agents=tuple(agents),
        xmpp_domain=xmpp_domain,
        xmpp_host=os.getenv("XMPP_HOST", "xmpp").strip(),
        xmpp_port=int(os.getenv("XMPP_PORT", "5222")),
        xmpp_auto_register=_env_bool("XMPP_AUTO_REGISTER", True),
        mcp_url=mcp_url,
        opensearch_url=opensearch_url,
        anomaly_watch_poll_seconds=anomaly_watch_poll_seconds,
        anomaly_watch_lookback_seconds=anomaly_watch_lookback_seconds,
        ollama_url=ollama_url,
        reasoning_model=reasoning_model,
        tool_model=tool_model,
        embedding_model=embedding_model,
        spade_llm_memory_path=spade_llm_memory_path,
        health_host=os.getenv("AGENT_HEALTH_HOST", "0.0.0.0").strip(),
        health_port=int(os.getenv("AGENT_HEALTH_PORT", "8081")),
        api_host=os.getenv("AGENT_API_HOST", "0.0.0.0").strip(),
        api_port=int(os.getenv("AGENT_API_PORT", "8082")),
        mongodb_uri=mongodb_uri,
        mongodb_database=mongodb_database,
        incident_correlation_window_seconds=incident_correlation_window_seconds,
        agentspeak_technical_lead_asl=agentspeak_technical_lead_asl,
        agentspeak_specialist_asl=agentspeak_specialist_asl,
        agentspeak_action_timeout_seconds=agentspeak_action_timeout_seconds,
        agentspeak_bdi_max_concurrency=agentspeak_bdi_max_concurrency,
        task_dispatch_timeout_seconds=task_dispatch_timeout_seconds,
        max_llm_concurrency=max_llm_concurrency,
        react_max_steps=react_max_steps,
        react_tool_timeout_seconds=react_tool_timeout_seconds,
        enable_test_anomaly_injection=enable_test_anomaly_injection,
        enable_opensearch_anomaly_watcher=enable_opensearch_anomaly_watcher,
    )
