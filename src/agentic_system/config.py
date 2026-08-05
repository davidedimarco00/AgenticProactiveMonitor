from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AgentCredentials(BaseModel):
    jid: str
    password: str


class OpenSearchSettings(BaseModel):
    base_url: str = Field(default_factory=lambda: os.getenv('OPENSEARCH_URL', 'http://opensearch:9200'))
    username: str | None = Field(default_factory=lambda: os.getenv('OPENSEARCH_USERNAME'))
    password: str | None = Field(default_factory=lambda: os.getenv('OPENSEARCH_PASSWORD'))
    verify_ssl: bool = False


class Settings(BaseModel):
    coordinator: AgentCredentials
    metrics: AgentCredentials
    logs: AgentCredentials
    topology: AgentCredentials
    hypothesis: AgentCredentials
    investigation: AgentCredentials
    diagnostic_executor: AgentCredentials
    critic: AgentCredentials
    opensearch: OpenSearchSettings = OpenSearchSettings()
    topology_file: str = 'src/agentic_system/config/topology.yaml'
    sync_detectors_on_start: bool = True


def load_settings(path: str | Path) -> Settings:
    with Path(path).open('r', encoding='utf-8') as handle:
        return Settings.model_validate(yaml.safe_load(handle))
