from __future__ import annotations

from spade_llm.mcp import StreamableHttpServerConfig

from ..reasoning import HybridLLMProvider
from ..settings import RuntimeConfig
from .application_engineer import ApplicationEngineerAgent
from .base import BaseAgent
from .network_engineer import NetworkEngineerAgent
from .software_developer import SoftwareDeveloperAgent
from .system_engineer import SystemEngineerAgent
from .technical_lead import TechnicalLeadAgent


ROLE_CONSTRUCTORS: dict[str, type[BaseAgent]] = {
    "technical_lead": TechnicalLeadAgent,
    "system_engineer": SystemEngineerAgent,
    "network_engineer": NetworkEngineerAgent,
    "application_engineer": ApplicationEngineerAgent,
    "software_developer": SoftwareDeveloperAgent,
}

MCP_SERVER_NAME = "apm_mcp"


def build_agents(config: RuntimeConfig) -> list[BaseAgent]:
    """Build the five project agents on top of SPADE-LLM official primitives."""

    provider = HybridLLMProvider.from_runtime(config)
    mcp_server = StreamableHttpServerConfig(
        name=MCP_SERVER_NAME,
        url=config.mcp_url,
        cache_tools=True,
    )

    agents: list[BaseAgent] = []
    for spec in config.agents:
        constructor = ROLE_CONSTRUCTORS.get(spec.role)
        if constructor is None:
            raise RuntimeError(f"No SPADE-LLM agent implementation for role: {spec.role}")

        common_kwargs = {
            "provider": provider,
            "mcp_servers": [mcp_server],
            "interaction_memory_path": config.spade_llm_memory_path,
        }
        if spec.role == "technical_lead":
            common_kwargs.update(
                {
                    "agentspeak_technical_lead_asl": config.agentspeak_technical_lead_asl,
                    "agentspeak_action_timeout_seconds": config.agentspeak_action_timeout_seconds,
                    "agentspeak_bdi_max_concurrency": config.agentspeak_bdi_max_concurrency,
                }
            )
        else:
            # Every specialist owns an independent AgentSpeak runtime instance.
            # The common plan defines the BDI lifecycle; role-specific expertise
            # remains in each agent's prompt and later ReAct execution.
            common_kwargs.update(
                {
                    "agentspeak_specialist_asl": config.agentspeak_specialist_asl,
                    "agentspeak_action_timeout_seconds": config.agentspeak_action_timeout_seconds,
                    "agentspeak_bdi_max_concurrency": config.agentspeak_bdi_max_concurrency,
                }
            )

        agents.append(
            constructor(
                spec.jid,
                spec.password,
                spec.display_name,
                spec.health_port,
                **common_kwargs,
            )
        )

    return agents
