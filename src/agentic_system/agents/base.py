from __future__ import annotations

import logging

from spade.agent import Agent

from ..workspace import IncidentWorkspace


class WorkspaceAgent(Agent):
    def __init__(self, jid: str, password: str, workspace: IncidentWorkspace, verify_security: bool = False):
        super().__init__(jid, password, verify_security=verify_security)
        self.workspace = workspace
        self.log = logging.getLogger(self.__class__.__name__)
