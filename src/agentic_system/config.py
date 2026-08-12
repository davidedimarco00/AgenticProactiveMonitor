from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentCredentials(BaseModel):
    jid: str
    password: str


class OpenSearchSettings(BaseModel):
    base_url: str = "http://opensearch:9200"
    username: str | None = None
    password: str | None = None
    verify_ssl: bool = False


class OllamaSettings(BaseModel):
    base_url: str = "http://host.docker.internal:11434"
    model: str = "llama3.2:3b"
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
    sync_detectors_on_start: bool = False
    detector_metrics: list[str] = ["usage_active", "used_percent"]
    open_demo_incident: bool = True


def _environment_overrides(data: dict[str, Any]) -> dict[str, Any]:
    opensearch = data.setdefault("opensearch", {})
    ollama = data.setdefault("ollama", {})

    if value := os.getenv("OPENSEARCH_URL"):
        opensearch["base_url"] = value
    if value := os.getenv("OPENSEARCH_USERNAME"):
        opensearch["username"] = value
    if value := os.getenv("OPENSEARCH_PASSWORD"):
        opensearch["password"] = value
    if value := os.getenv("OLLAMA_BASE_URL"):
        ollama["base_url"] = value
    if value := os.getenv("OLLAMA_MODEL"):
        ollama["model"] = value

    if "AGENTIC_OPEN_DEMO_INCIDENT" in os.environ:
        data["open_demo_incident"] = os.environ["AGENTIC_OPEN_DEMO_INCIDENT"].lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    return data


def load_settings(path: str | Path) -> Settings:
    raw = Path(path).read_text(encoding="utf-8")
    expanded = os.path.expandvars(raw)
    data = yaml.safe_load(expanded) or {}
    return Settings.model_validate(_environment_overrides(data))
