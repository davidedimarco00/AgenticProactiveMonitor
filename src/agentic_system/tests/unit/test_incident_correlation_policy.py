from datetime import datetime, timedelta, timezone

from agentic_system.domain.incidents import IncidentCorrelationPolicy


def _incident(*, detector_id: str = "detector-1", status: str = "NEW", updated_at: str) -> dict:
    return {
        "incident_id": "INC-TEST",
        "status": status,
        "updated_at": updated_at,
        "anomaly": {"detector_id": detector_id},
    }


def test_same_single_entity_detector_within_window_is_correlated() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    policy = IncidentCorrelationPolicy(window_seconds=600)
    incident = _incident(updated_at=(now - timedelta(minutes=5)).isoformat())

    assert policy.can_correlate(incident, detector_id="detector-1", now=now) is True


def test_triaged_incident_remains_correlatable_within_window() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    policy = IncidentCorrelationPolicy(window_seconds=600)
    incident = _incident(
        status="TRIAGED",
        updated_at=(now - timedelta(minutes=2)).isoformat(),
    )

    assert policy.can_correlate(incident, detector_id="detector-1", now=now) is True


def test_different_detector_is_not_correlated() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    policy = IncidentCorrelationPolicy(window_seconds=600)
    incident = _incident(updated_at=now.isoformat())

    assert policy.can_correlate(incident, detector_id="detector-2", now=now) is False


def test_stale_or_closed_incident_is_not_correlated() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    policy = IncidentCorrelationPolicy(window_seconds=600)

    stale = _incident(updated_at=(now - timedelta(minutes=11)).isoformat())
    closed = _incident(status="RESOLVED", updated_at=now.isoformat())

    assert policy.can_correlate(stale, detector_id="detector-1", now=now) is False
    assert policy.can_correlate(closed, detector_id="detector-1", now=now) is False
