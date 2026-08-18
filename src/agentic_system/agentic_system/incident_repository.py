"""Backward-compatible facade for MongoDB incident persistence."""

from .infrastructure.mongodb import (
    ACTIVE_STATUSES,
    IncidentRepository,
    deep_merge,
    format_incident_id,
    new_event_id,
    new_incident_id,
    normalize_event,
    normalize_incident,
    public_document,
    sanitize_event_payload,
    sanitize_incident_payload,
    utc_now,
)

__all__ = [
    "ACTIVE_STATUSES",
    "IncidentRepository",
    "deep_merge",
    "format_incident_id",
    "new_event_id",
    "new_incident_id",
    "normalize_event",
    "normalize_incident",
    "public_document",
    "sanitize_event_payload",
    "sanitize_incident_payload",
    "utc_now",
]
