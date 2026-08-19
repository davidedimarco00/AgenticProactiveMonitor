from __future__ import annotations

from typing import Any

from ..incidents import AnomalyObservation


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def anomaly_observation_from_hit(hit: dict[str, Any]) -> AnomalyObservation | None:
    """Map an OpenSearch anomaly result into the technology-independent domain object."""

    source = hit.get("_source")
    if not isinstance(source, dict):
        return None

    result_id = str(hit.get("_id") or "").strip()
    result_index = str(hit.get("_index") or "").strip()
    detector_id = str(source.get("detector_id") or "").strip()

    try:
        anomaly_grade = float(source.get("anomaly_grade") or 0.0)
        confidence = float(source.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None

    if not result_id or not result_index or not detector_id or anomaly_grade <= 0.0:
        return None

    try:
        return AnomalyObservation(
            result_id=result_id,
            result_index=result_index,
            detector_id=detector_id,
            anomaly_grade=anomaly_grade,
            confidence=confidence,
            anomaly_score=_optional_float(source.get("anomaly_score")),
            data_start_time=_optional_int(source.get("data_start_time")),
            data_end_time=_optional_int(source.get("data_end_time")),
            execution_start_time=_optional_int(source.get("execution_start_time")),
            execution_end_time=_optional_int(source.get("execution_end_time")),
        )
    except (TypeError, ValueError):
        return None
