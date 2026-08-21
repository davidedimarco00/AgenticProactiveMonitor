from __future__ import annotations

from spade_llm.mcp import StreamableHttpServerConfig

from ..reasoning import RoleLLMProvider, SharedInferenceGate
from ..settings import RuntimeConfig
from .base import BaseAgent
from .prompts import specialist_system_prompt
from .specialist import SpecialistAgent
from .technical_lead import TechnicalLeadAgent


MCP_SERVER_NAME = "apm_mcp"
MIN_DIAGNOSTIC_REACT_STEPS = 10


def build_agents(config: RuntimeConfig) -> list[BaseAgent]:
    """Build one TL plus four configured specialists with explicit model roles.

    Gemma is reserved for Technical Lead triage/review. Qwen is assigned directly
    to specialists, where LangChain owns the operational ReAct/tool loop. Both
    providers share the same embedding model and backend-wide Ollama gate.
    """

    gate = SharedInferenceGate(config.max_llm_concurrency)
    technical_lead_provider = RoleLLMProvider(
        model=config.reasoning_model,
        base_url=config.ollama_url,
        embedding_model=config.embedding_model,
        gate=gate,
    )
    specialist_provider = RoleLLMProvider(
        model=config.tool_model,
        base_url=config.ollama_url,
        embedding_model=config.embedding_model,
        gate=gate,
    )
    mcp_server = StreamableHttpServerConfig(
        name=MCP_SERVER_NAME,
        url=config.mcp_url,
        cache_tools=True,
    )

    agents: list[BaseAgent] = []
    for spec in config.agents:
        common_kwargs = {
            "mcp_servers": [mcp_server],
            "interaction_memory_path": config.spade_llm_memory_path,
        }

        if spec.role == "technical_lead":
            agent: BaseAgent = TechnicalLeadAgent(
                spec.jid,
                spec.password,
                spec.display_name,
                spec.health_port,
                provider=technical_lead_provider,
                agentspeak_technical_lead_asl=config.agentspeak_technical_lead_asl,
                agentspeak_action_timeout_seconds=config.agentspeak_action_timeout_seconds,
                agentspeak_bdi_max_concurrency=config.agentspeak_bdi_max_concurrency,
                **common_kwargs,
            )
        else:
            specialist = SpecialistAgent(
                spec.jid,
                spec.password,
                spec.display_name,
                spec.health_port,
                role=spec.role,
                system_prompt=specialist_system_prompt(spec.role),
                provider=specialist_provider,
                agentspeak_specialist_asl=config.agentspeak_specialist_asl,
                agentspeak_action_timeout_seconds=config.agentspeak_action_timeout_seconds,
                agentspeak_bdi_max_concurrency=config.agentspeak_bdi_max_concurrency,
                **common_kwargs,
            )
            specialist.configure_react(
                max_steps=max(config.react_max_steps, MIN_DIAGNOSTIC_REACT_STEPS),
                tool_timeout_seconds=config.react_tool_timeout_seconds,
            )
            agent = specialist
        agents.append(agent)

    return agents
