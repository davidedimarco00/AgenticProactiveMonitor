from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class AgentCredentials(BaseModel):
    jid: str
    password: str


class Settings(BaseModel):
    coordinator: AgentCredentials
    metrics: AgentCredentials
    logs: AgentCredentials
    hypothesis: AgentCredentials
    critic: AgentCredentials


def load_settings(path: str | Path) -> Settings:
    with Path(path).open("r", encoding="utf-8") as handle:
        return Settings.model_validate(yaml.safe_load(handle))
