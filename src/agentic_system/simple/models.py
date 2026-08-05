from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    COLLECTING_EVIDENCE = "collecting_evidence"
    REASONING = "reasoning"
    REVIEWING = "reviewing"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    FAILED = "failed"


class DiagnosticCheck(BaseModel):
    action: str
    target: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    cause: str
    component: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    hypotheses: list[Hypothesis]
    preferred_hypothesis: Hypothesis
    required_checks: list[DiagnosticCheck] = Field(default_factory=list)
    explanation: str


class CriticReview(BaseModel):
    accepted: bool
    reason: str
    required_checks: list[DiagnosticCheck] = Field(default_factory=list)


class IncidentContext(BaseModel):
    incident_id: str = Field(default_factory=lambda: uuid4().hex)
    detector_id: str
    host_id: str
    metric_name: str
    anomaly_score: float
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: IncidentStatus = IncidentStatus.DETECTED
    round: int = 0
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    critic_review: CriticReview | None = None
    remediation: dict[str, Any] | None = None
