from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnomalyInfo(BaseModel):
    # Keep only anomaly metadata required to identify and classify the incident.
    # Raw metrics and logs stay in OpenSearch.
    model_config = ConfigDict(extra="ignore")

    detector_id: str | None = None
    detector_name: str | None = None
    anomaly_type: str | None = None
    grade: float | None = None
    confidence: float | None = None


class DiagnosisInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str | None = None
    root_cause: str | None = None
    confidence: float | None = None
    evidence: list[str] = Field(default_factory=list)


class RemediationInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str | None = None
    status: str | None = None
    steps: list[Any] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ValidationInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    summary: str | None = None


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    incident_id: str | None = None
    status: str = "NEW"
    severity: str = "MEDIUM"
    entity: str | None = None
    service: str | None = None
    machine_role: str | None = None
    takeover_reason: str | None = None
    takeover_factors: list[str] = Field(default_factory=list)
    anomaly: AnomalyInfo = Field(default_factory=AnomalyInfo)
    diagnosis: DiagnosisInfo = Field(default_factory=DiagnosisInfo)
    remediation: RemediationInfo = Field(default_factory=RemediationInfo)
    validation: ValidationInfo = Field(default_factory=ValidationInfo)
    agentic: dict[str, Any] = Field(default_factory=dict)
    detected_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None


class IncidentPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    severity: str | None = None
    entity: str | None = None
    service: str | None = None
    machine_role: str | None = None
    takeover_reason: str | None = None
    takeover_factors: list[str] | None = None
    anomaly: AnomalyInfo | None = None
    diagnosis: DiagnosisInfo | None = None
    remediation: RemediationInfo | None = None
    validation: ValidationInfo | None = None
    agentic: dict[str, Any] | None = None
    detected_at: str | None = None
    closed_at: str | None = None


class IncidentEventCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str | None = None
    timestamp: str | None = None
    event_type: str
    agent_role: str | None = None
    agent_jid: str | None = None
    action: str | None = None
    called_by: str | None = None
    reason: str | None = None
    description: str | None = None
    tool: str | None = None
    status: str | None = None
    outcome: str | None = None
