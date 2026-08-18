from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.server_api import ServerApi


ACTIVE_STATUSES = {
    "NEW",
    "TAKEN_IN_CHARGE",
    "UNDER_ANALYSIS",
    "DIAGNOSED",
    "OPERATOR_ACTION_REQUIRED",
}

INCIDENT_FIELDS = {
    "incident_id",
    "status",
    "severity",
    "entity",
    "service",
    "machine_role",
    "takeover_reason",
    "takeover_factors",
    "anomaly",
    "diagnosis",
    "remediation",
    "validation",
    "agentic",
    "detected_at",
    "created_at",
    "updated_at",
    "closed_at",
}
ANOMALY_FIELDS = {"detector_id", "anomaly_type", "grade", "confidence"}
DIAGNOSIS_FIELDS = {"summary", "root_cause", "confidence", "evidence"}
REMEDIATION_FIELDS = {"summary", "status", "steps", "verification", "risks"}
VALIDATION_FIELDS = {"status", "summary"}
AGENTIC_FIELDS = {"current_agent", "active_agents"}
EVENT_FIELDS = {
    "event_id",
    "timestamp",
    "event_type",
    "agent_role",
    "agent_jid",
    "action",
    "called_by",
    "reason",
    "description",
    "tool",
    "status",
    "outcome",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _keep_fields(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: deepcopy(item) for key, item in value.items() if key in allowed}


def sanitize_incident_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep agentic incident conclusions while excluding raw observability payloads."""

    doc = {key: deepcopy(value) for key, value in payload.items() if key in INCIDENT_FIELDS}
    if "anomaly" in doc:
        doc["anomaly"] = _keep_fields(doc["anomaly"], ANOMALY_FIELDS)
    if "diagnosis" in doc:
        doc["diagnosis"] = _keep_fields(doc["diagnosis"], DIAGNOSIS_FIELDS)
    if "remediation" in doc:
        doc["remediation"] = _keep_fields(doc["remediation"], REMEDIATION_FIELDS)
    if "validation" in doc:
        doc["validation"] = _keep_fields(doc["validation"], VALIDATION_FIELDS)
    if "agentic" in doc:
        doc["agentic"] = _keep_fields(doc["agentic"], AGENTIC_FIELDS)
    return doc


def sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in payload.items() if key in EVENT_FIELDS}


def new_incident_id() -> str:
    return f"INC-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6].upper()}"


def new_event_id() -> str:
    return f"AEV-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8].upper()}"


def normalize_incident(payload: dict[str, Any], incident_id: str | None = None) -> dict[str, Any]:
    now = utc_now()
    doc = sanitize_incident_payload(payload)
    doc["incident_id"] = incident_id or doc.get("incident_id") or new_incident_id()
    doc.setdefault("created_at", now)
    doc.setdefault("detected_at", doc["created_at"])
    doc["updated_at"] = now
    doc["status"] = str(doc.get("status") or "NEW").upper()
    doc["severity"] = str(doc.get("severity") or "MEDIUM").upper()
    doc.setdefault("entity", doc.get("service") or "unknown")
    doc.setdefault("service", doc.get("entity") or "unknown")
    doc.setdefault("machine_role", "unknown")
    doc.setdefault("takeover_reason", "")
    doc.setdefault("takeover_factors", [])
    doc.setdefault("anomaly", {})
    doc.setdefault("diagnosis", {})
    doc.setdefault("remediation", {})
    doc.setdefault("validation", {})
    doc.setdefault("agentic", {})
    return doc


def normalize_event(payload: dict[str, Any], incident_id: str) -> dict[str, Any]:
    doc = sanitize_event_payload(payload)
    doc["event_id"] = doc.get("event_id") or new_event_id()
    doc["incident_id"] = incident_id
    doc["timestamp"] = doc.get("timestamp") or utc_now()
    doc["event_type"] = str(doc.get("event_type") or "agent_activity").upper()
    if doc.get("status"):
        doc["status"] = str(doc["status"]).upper()
    if doc.get("description") and not doc.get("reason"):
        doc["reason"] = doc["description"]
    return doc


def public_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    clean = deepcopy(document)
    clean.pop("_id", None)
    return clean


class IncidentRepository:
    """MongoDB persistence for agentic incidents and their append-only event history."""

    def __init__(self, uri: str, database_name: str) -> None:
        self.client = AsyncMongoClient(
            uri,
            server_api=ServerApi("1"),
        )
        self.database = self.client[database_name]
        self.incidents = self.database["incidents"]
        self.events = self.database["incident_events"]

    async def connect(self) -> None:
        await self.client.admin.command({"ping": 1})
        await self.incidents.create_index("incident_id", unique=True)
        await self.incidents.create_index([("updated_at", DESCENDING)])
        await self.incidents.create_index([("status", ASCENDING), ("updated_at", DESCENDING)])
        await self.events.create_index("event_id", unique=True)
        await self.events.create_index([("incident_id", ASCENDING), ("timestamp", ASCENDING)])
        await self.events.create_index([("agent_role", ASCENDING), ("timestamp", DESCENDING)])

    async def ping(self) -> bool:
        try:
            await self.client.admin.command({"ping": 1})
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()

    async def list_incidents(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        mongo_query: dict[str, Any] = {}
        if status:
            mongo_query["status"] = status.upper()
        if query:
            pattern = re.escape(query.strip())
            regex = {"$regex": pattern, "$options": "i"}
            mongo_query["$or"] = [
                {"incident_id": regex},
                {"entity": regex},
                {"service": regex},
                {"takeover_reason": regex},
                {"diagnosis.summary": regex},
                {"diagnosis.root_cause": regex},
                {"remediation.summary": regex},
            ]

        bounded_limit = min(max(limit, 1), 500)
        cursor = self.incidents.find(mongo_query).sort("updated_at", DESCENDING).limit(bounded_limit)
        documents: list[dict[str, Any]] = []
        async for document in cursor:
            documents.append(public_document(document) or {})
        return documents

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        document = await self.incidents.find_one({"incident_id": incident_id})
        return public_document(document)

    async def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        incident = normalize_incident(payload)
        incident["_id"] = incident["incident_id"]
        await self.incidents.insert_one(incident)
        return public_document(incident) or {}

    async def update_incident(self, incident_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        current = await self.get_incident(incident_id)
        if current is None:
            return None

        merged = deep_merge(current, sanitize_incident_payload(patch))
        merged = normalize_incident(merged, incident_id=incident_id)
        merged["created_at"] = current.get("created_at", merged["created_at"])
        if merged.get("status") in {"RESOLVED", "CLOSED"} and not merged.get("closed_at"):
            merged["closed_at"] = utc_now()
        merged["_id"] = incident_id
        await self.incidents.replace_one({"incident_id": incident_id}, merged)
        return public_document(merged)

    async def add_event(self, incident_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if await self.get_incident(incident_id) is None:
            return None
        event = normalize_event(payload, incident_id)
        event["_id"] = event["event_id"]
        await self.events.insert_one(event)
        await self.incidents.update_one(
            {"incident_id": incident_id},
            {"$set": {"updated_at": event["timestamp"]}},
        )
        return public_document(event)

    async def list_events(
        self,
        *,
        incident_id: str | None = None,
        agent_role: str | None = None,
        limit: int = 200,
        ascending: bool = True,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if incident_id:
            query["incident_id"] = incident_id
        if agent_role:
            query["agent_role"] = agent_role
        bounded_limit = min(max(limit, 1), 500)
        direction = ASCENDING if ascending else DESCENDING
        cursor = self.events.find(query).sort("timestamp", direction).limit(bounded_limit)
        documents: list[dict[str, Any]] = []
        async for document in cursor:
            documents.append(public_document(document) or {})
        return documents
