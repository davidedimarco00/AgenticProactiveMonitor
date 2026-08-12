from __future__ import annotations

from collections.abc import Callable

from ..config import AgentSpec
from .base import BaseRoleAgent
from .roles import (
    ApplicationEngineerAgent,
    NetworkEngineerAgent,
    SoftwareDeveloperAgent,
    SystemEngineerAgent,
    TechnicalLeadAgent,
)


AgentConstructor = Callable[[str, str, str, int], BaseRoleAgent]

ROLE_CONSTRUCTORS: dict[str, AgentConstructor] = {
    "technical_lead": TechnicalLeadAgent,
    "system_engineer": SystemEngineerAgent,
    "network_engineer": NetworkEngineerAgent,
    "application_engineer": ApplicationEngineerAgent,
    "software_developer": SoftwareDeveloperAgent,
}


def build_agents(specs: tuple[AgentSpec, ...]) -> list[BaseRoleAgent]:
    agents: list[BaseRoleAgent] = []

    for spec in specs:
        constructor = ROLE_CONSTRUCTORS.get(spec.role)
        if constructor is None:
            raise RuntimeError(f"No SPADE agent implementation for role: {spec.role}")

        agents.append(
            constructor(
                spec.jid,
                spec.password,
                spec.display_name,
                spec.health_port,
            )
        )

    return agents
