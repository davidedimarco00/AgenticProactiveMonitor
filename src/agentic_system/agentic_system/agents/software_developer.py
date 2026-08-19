from __future__ import annotations

from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from .prompts import SOFTWARE_DEVELOPER_SYSTEM_PROMPT
from .specialist import SpecialistAgent


class SoftwareDeveloperAgent(SpecialistAgent):
    SYSTEM_PROMPT = SOFTWARE_DEVELOPER_SYSTEM_PROMPT

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
    ) -> None:
        super().__init__(
            jid,
            password,
            display_name,
            health_port,
            role="software_developer",
            system_prompt=self.SYSTEM_PROMPT,
            provider=provider,
            mcp_servers=mcp_servers,
            interaction_memory_path=interaction_memory_path,
        )
