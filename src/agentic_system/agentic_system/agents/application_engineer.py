from __future__ import annotations

from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from .prompts import APPLICATION_ENGINEER_SYSTEM_PROMPT
from .specialist import SpecialistAgent


class ApplicationEngineerAgent(SpecialistAgent):
    SYSTEM_PROMPT = APPLICATION_ENGINEER_SYSTEM_PROMPT

    def __init__(
        self,
        jid: str,
        password: str,
        display_name: str,
        health_port: int,
        *,
        provider: LLMProvider,
        mcp_servers: list[MCPServerConfig],
        interaction_memory_path: str,
        agentspeak_specialist_asl: str,
        agentspeak_action_timeout_seconds: float,
        agentspeak_bdi_max_concurrency: int,
    ) -> None:
        super().__init__(
            jid,
            password,
            display_name,
            health_port,
            role="application_engineer",
            system_prompt=self.SYSTEM_PROMPT,
            provider=provider,
            mcp_servers=mcp_servers,
            interaction_memory_path=interaction_memory_path,
            agentspeak_specialist_asl=agentspeak_specialist_asl,
            agentspeak_action_timeout_seconds=agentspeak_action_timeout_seconds,
            agentspeak_bdi_max_concurrency=agentspeak_bdi_max_concurrency,
        )
