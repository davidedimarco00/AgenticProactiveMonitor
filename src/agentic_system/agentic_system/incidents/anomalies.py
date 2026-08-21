from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnomalyObservation:
    """Normalized anomaly metadata entering the autonomous agentic workflow.

    This object is independent from OpenSearch transport details and intentionally
    excludes raw metrics, raw logs and raw tool payloads. Human-readable detector
    metadata is carried with the observation so the durable inbox remains useful
    to operators without exposing only opaque OpenSearch identifiers.
    `recovery_incident_id` is populated only for synthetic startup work items.
    `source` distinguishes production OpenSearch observations from explicit test
    injections without changing the downstream workflow semantics.
    """

    result_id: str
    result_index: str
    detector_id: str
    anomaly_grade: float
    confidence: float
    anomaly_score: float | None
    data_start_time: int | None
    data_end_time: int | None
    execution_start_time: int | None
    execution_end_time: int | None
    recovery_incident_id: str | None = None
    detector_name: str | None = None
    detector_description: str | None = None
    detector_indices: tuple[str, ...] = ()
    source: str = "opensearch"

    @property
    def deduplication_key(self) -> str:
        return f"{self.result_index}:{self.result_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnomalyObservation":
        """Restore a normalized observation from the durable MongoDB inbox."""

        def optional_float(value: Any) -> float | None:
            if value is None:
                return None
            return float(value)

        def optional_int(value: Any) -> int | None:
            if value is None:
                return None
            return int(value)

        raw_indices = payload.get("detector_indices") or []
        if not isinstance(raw_indices, (list, tuple)):
            raw_indices = []

        return cls(
            result_id=str(payload.get("result_id") or "").strip(),
            result_index=str(payload.get("result_index") or "").strip(),
            detector_id=str(payload.get("detector_id") or "").strip(),
            anomaly_grade=float(payload.get("anomaly_grade") or 0.0),
            confidence=float(payload.get("confidence") or 0.0),
            anomaly_score=optional_float(payload.get("anomaly_score")),
            data_start_time=optional_int(payload.get("data_start_time")),
            data_end_time=optional_int(payload.get("data_end_time")),
            execution_start_time=optional_int(payload.get("execution_start_time")),
            execution_end_time=optional_int(payload.get("execution_end_time")),
            recovery_incident_id=(
                str(payload.get("recovery_incident_id") or "").strip() or None
            ),
            detector_name=str(payload.get("detector_name") or "").strip() or None,
            detector_description=(
                str(payload.get("detector_description") or "").strip() or None
            ),
            detector_indices=tuple(str(index) for index in raw_indices),
            source=str(payload.get("source") or "opensearch").strip().lower()
            or "opensearch",
        )
