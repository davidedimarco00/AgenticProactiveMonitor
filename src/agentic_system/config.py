from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AgentCredentials(BaseModel):
    jid: str
    password: str


class OpenSearchSettings(BaseModel):
    base_url: str = Field(default_factory=lambda: os.getenv("OPENSEARCH_URL", "https://opensearch:9200"))
    username: str | None = Field(default_factory=lambda: os.getenv("OPENSEARCH_USERNAME"))
    password: str | None = Field(default_factory=lambda: os.getenv("OPENSEARCH_PASSWORD"))
    verify_ssl: bool = False


class OllamaSettings(BaseModel):
    base_url: str = Field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"))
    model: str = Field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
    temperature: float = 0.1
    timeout_seconds: float = 120.0
    keep_alive: str = "5m"
    max_retries: int = Field(default=1, ge=0, le=3)
    check_model_on_start: bool = True


class Settings(BaseModel):
    coordinator: AgentCredentials
    evidence: AgentCredentials
    reasoning: AgentCredentials
    critic: AgentCredentials
    remediation: AgentCredentials
    opensearch: OpenSearchSettings = OpenSearchSettings()
    ollama: OllamaSettings = OllamaSettings()
    topology_file: str = "src/agentic_system/config/topology.yaml"
    sync_detectors_on_start: bool = True
    detector_metrics: list[str] = [
        "cpu.usage_active",
        "mem.used_percent",
        "disk.used_percent",
    ]
    open_demo_incident: bool = True


def load_settings(path: str | Path) -> Settings:
    raw = Path(path).read_text(encoding="utf-8")
    expanded = os.path.expandvars(raw)
    return Settings.model_validate(yaml.safe_load(expanded))
