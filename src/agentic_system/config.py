from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AgentCredentials(BaseModel):
    jid: str
    password: str


class OpenSearchSettings(BaseModel):
    url: str = "http://opensearch:9200"
    username: str | None = None
    password: str | None = None
    verify_ssl: bool = False
    metrics_index: str = "metrics-*"
    logs_index: str = "logs-*"


class Settings(BaseModel):
    coordinator: AgentCredentials
    metrics: AgentCredentials
    logs: AgentCredentials
    topology: AgentCredentials
    hypothesis: AgentCredentials
    critic: AgentCredentials
    investigation: AgentCredentials
    diagnostic_executor: AgentCredentials
    opensearch: OpenSearchSettings = Field(default_factory=OpenSearchSettings)
    topology_map: dict[str, list[str]] = Field(default_factory=dict)


def load_settings(path: str | Path) -> Settings:
    with Path(path).open("r", encoding="utf-8") as handle:
        return Settings.model_validate(yaml.safe_load(handle))
