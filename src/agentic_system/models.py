from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    FAILED = "failed"


class EvidenceType(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TOPOLOGY = "topology"
    KNOWLEDGE = "knowledge"
    DIAGNOSTIC_TEST = "diagnostic_test"


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    incident_id: str
    source_agent: str
    evidence_type: EvidenceType
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: uuid4().hex)
    incident_id: str
    statement: str
    suspected_component: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rejected: bool = False
    confirmed: bool = False


class DiagnosticTest(BaseModel):
    test_id: str = Field(default_factory=lambda: uuid4().hex)
    incident_id: str
    hypothesis_id: str
    action: str
    target: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str


class Incident(BaseModel):
    incident_id: str = Field(default_factory=lambda: uuid4().hex)
    detector_id: str
    host_id: str
    metric_name: str
    anomaly_score: float = Field(ge=0.0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: IncidentStatus = IncidentStatus.DETECTED
    investigation_round: int = 0
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    diagnostic_tests: list[DiagnosticTest] = Field(default_factory=list)
    confirmed_hypothesis_id: str | None = None
