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
    ollama_url: str
    reasoning_model: str
    tool_model: str
    fallback_tool_model: str
    llm_context: int
    max_llm_concurrency: int
    health_host: str
    health_port: int


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

    return RuntimeConfig(
        agents=tuple(agents),
        xmpp_domain=xmpp_domain,
        xmpp_host=os.getenv("XMPP_HOST", "xmpp").strip(),
        xmpp_port=int(os.getenv("XMPP_PORT", "5222")),
        xmpp_auto_register=_env_bool("XMPP_AUTO_REGISTER", True),
        mcp_url=os.getenv("MCP_URL", "http://mcp-server:8000/mcp").strip(),
        ollama_url=os.getenv(
            "OLLAMA_URL", "http://host.docker.internal:11434"
        ).strip(),
        reasoning_model=os.getenv("OLLAMA_REASONING_MODEL", "gemma4:e2b").strip(),
        tool_model=os.getenv("OLLAMA_TOOL_MODEL", "qwen3.5:4b").strip(),
        fallback_tool_model=os.getenv(
            "OLLAMA_FALLBACK_TOOL_MODEL", "qwen2.5:latest"
        ).strip(),
        llm_context=int(os.getenv("AGENT_LLM_CONTEXT", "8192")),
        max_llm_concurrency=int(os.getenv("AGENT_MAX_LLM_CONCURRENCY", "2")),
        health_host=os.getenv("AGENT_HEALTH_HOST", "0.0.0.0").strip(),
        health_port=int(os.getenv("AGENT_HEALTH_PORT", "8081")),
    )
