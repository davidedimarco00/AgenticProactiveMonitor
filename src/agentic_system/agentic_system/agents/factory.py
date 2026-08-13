from __future__ import annotations

from spade_llm.mcp import StreamableHttpServerConfig
from spade_llm.providers import LLMProvider

from ..config import RuntimeConfig
from ..providers import HybridLLMProvider
from .base import BaseAgent
from .roles import (
    ApplicationEngineerAgent,
    NetworkEngineerAgent,
    SoftwareDeveloperAgent,
    SystemEngineerAgent,
    TechnicalLeadAgent,
)


ROLE_CONSTRUCTORS: dict[str, type[BaseAgent]] = {
    "technical_lead": TechnicalLeadAgent,
    "system_engineer": SystemEngineerAgent,
    "network_engineer": NetworkEngineerAgent,
    "application_engineer": ApplicationEngineerAgent,
    "software_developer": SoftwareDeveloperAgent,
}


def build_agents(config: RuntimeConfig) -> list[BaseAgent]:
    """Build the five project agents on top of SPADE-LLM official primitives."""

    reasoning_provider = LLMProvider(
        model=f"ollama/{config.reasoning_model}",
        base_url=config.ollama_url,
    )
    provider = reasoning_provider
    mcp_server = StreamableHttpServerConfig(
        name="AgenticProactiveMonitor MCP",
        url=config.mcp_url,
        cache_tools=True,
    )

    agents: list[BaseAgent] = []
    for spec in config.agents:
        constructor = ROLE_CONSTRUCTORS.get(spec.role)
        if constructor is None:
            raise RuntimeError(f"No SPADE-LLM agent implementation for role: {spec.role}")

        agents.append(
            constructor(
                spec.jid,
                spec.password,
                spec.display_name,
                spec.health_port,
                provider=provider,
                mcp_servers=[mcp_server],
                interaction_memory_path=config.spade_llm_memory_path,
            )
        )

    return agents
