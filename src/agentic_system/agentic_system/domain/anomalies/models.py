from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnomalyObservation:
    """Normalized anomaly metadata entering the autonomous agentic workflow.

    This object is independent from OpenSearch transport details and intentionally
    excludes raw metrics, raw logs and raw tool payloads.
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

    @property
    def deduplication_key(self) -> str:
        return f"{self.result_index}:{self.result_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
