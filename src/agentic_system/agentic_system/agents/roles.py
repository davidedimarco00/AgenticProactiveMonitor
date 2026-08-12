from __future__ import annotations

from .base import BaseRoleAgent


class TechnicalLeadAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="technical_lead",
            display_name=display_name,
        )


class SystemEngineerAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="system_engineer",
            display_name=display_name,
        )


class NetworkEngineerAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="network_engineer",
            display_name=display_name,
        )


class ApplicationEngineerAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="application_engineer",
            display_name=display_name,
        )


class SoftwareDeveloperAgent(BaseRoleAgent):
    def __init__(self, jid: str, password: str, display_name: str) -> None:
        super().__init__(
            jid,
            password,
            role="software_developer",
            display_name=display_name,
        )
