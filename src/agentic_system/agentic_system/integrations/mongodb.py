from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.server_api import ServerApi


ACTIVE_STATUSES = {
    "NEW",
    "TAKEN_IN_CHARGE",
    "TRIAGED",
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
ANOMALY_FIELDS = {
    "detector_id",
    "detector_name",
    "anomaly_type",
    "grade",
    "confidence",
}
DIAGNOSIS_FIELDS = {"summary", "root_cause", "confidence", "evidence"}
REMEDIATION_FIELDS = {"summary", "status", "steps", "verification", "risks"}
VALIDATION_FIELDS = {"status", "summary"}
AGENTIC_FIELDS = {
    "current_agent",
    "active_agents",
    "primary_investigator",
    "triage_domain",
    "triage_confidence",
    "triage_rationale",
    "bdi_goal",
    "bdi_triage_intention",
    "bdi_intention",
    "investigation_task_id",
    "task_state",
    "task_dispatch_correlation_id",
    "specialist_bdi_goal",
    "specialist_bdi_acceptance_intention",
    "specialist_bdi_intention",
    "review_state",
    "review_decision",
    "review_confidence",
    "review_rationale",
    "bdi_review_goal",
    "bdi_review_intention",
    "bdi_review_decision_intention",
    "support_requested",
    "support_domain",
    "support_reason",
    "collaboration_roles",
    "collaboration_round",
    "peer_collaboration_state",
}
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
    "task_id",
    "task_state",
    "correlation_id",
    "bdi_goal",
    "bdi_intention",
}
TASK_FIELDS = {
    "task_id",
    "incident_id",
    "task_type",
    "created_by",
    "assigned_to",
    "state",
    "attempt",
    "max_attempts",
    "idempotency_key",
    "last_error",
    "outcome",
    "created_at",
    "updated_at",
    "finished_at",
}
TASK_ERROR_FIELDS = {"type", "message", "retryable", "at"}
TASK_OUTCOME_FIELDS = {"status", "summary", "result_ref"}
TERMINAL_TASK_STATES = {"COMPLETED", "FAILED"}


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


def sanitize_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    doc = {key: deepcopy(value) for key, value in payload.items() if key in TASK_FIELDS}
    if "last_error" in doc and doc["last_error"] is not None:
        doc["last_error"] = _keep_fields(doc["last_error"], TASK_ERROR_FIELDS)
    if "outcome" in doc and doc["outcome"] is not None:
        doc["outcome"] = _keep_fields(doc["outcome"], TASK_OUTCOME_FIELDS)
    return doc


def format_incident_id(day: str, sequence: int) -> str:
    """Build the operator-facing incident identifier from a UTC day and sequence."""

    if not re.fullmatch(r"\d{8}", day):
        raise ValueError("day must use YYYYMMDD format")
    if sequence <= 0:
        raise ValueError("sequence must be greater than zero")
    return f"INC-{day}-{sequence:03d}"


def new_incident_id() -> str:
    """Compatibility fallback for callers outside the Mongo repository.

    Persisted incidents use the repository's atomic daily counter instead.
    """

    return f"INC-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def new_event_id() -> str:
    return f"AEV-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8].upper()}"


def new_task_id() -> str:
    return f"TASK-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8].upper()}"


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


def normalize_task(payload: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
    now = utc_now()
    doc = sanitize_task_payload(payload)
    doc["task_id"] = task_id or doc.get("task_id") or new_task_id()
    doc["incident_id"] = str(doc.get("incident_id") or "").strip()
    doc["task_type"] = str(doc.get("task_type") or "").strip().upper()
    doc["created_by"] = str(doc.get("created_by") or "technical_lead").strip().lower()
    doc["assigned_to"] = str(doc.get("assigned_to") or "").strip().lower()
    doc["state"] = str(doc.get("state") or "PENDING").strip().upper()
    doc["attempt"] = max(int(doc.get("attempt") or 0), 0)
    doc["max_attempts"] = max(int(doc.get("max_attempts") or 3), 1)
    doc["idempotency_key"] = str(doc.get("idempotency_key") or "").strip()
    doc.setdefault("last_error", None)
    doc.setdefault("outcome", None)
    doc.setdefault("created_at", now)
    doc["updated_at"] = now
    if doc["state"] in TERMINAL_TASK_STATES:
        doc.setdefault("finished_at", now)
    else:
        doc.setdefault("finished_at", None)

    if not doc["incident_id"]:
        raise ValueError("Agent task incident_id is required")
    if not doc["task_type"]:
        raise ValueError("Agent task task_type is required")
    if not doc["assigned_to"]:
        raise ValueError("Agent task assigned_to is required")
    if not doc["idempotency_key"]:
        raise ValueError("Agent task idempotency_key is required")
    return doc


def public_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    clean = deepcopy(document)
    clean.pop("_id", None)
    return clean


class IncidentRepository:
    """MongoDB persistence for incidents, events and durable agent tasks."""

    def __init__(self, uri: str, database_name: str) -> None:
        self.client = AsyncMongoClient(uri, server_api=ServerApi("1"))
        self.database = self.client[database_name]
        self.incidents = self.database["incidents"]
        self.events = self.database["incident_events"]
        self.tasks = self.database["agent_tasks"]
        self.counters = self.database["counters"]

    async def connect(self) -> None:
        await self.client.admin.command({"ping": 1})
        await self.incidents.create_index("incident_id", unique=True)
        await self.incidents.create_index([("updated_at", DESCENDING)])
        await self.incidents.create_index([("status", ASCENDING), ("updated_at", DESCENDING)])
        await self.incidents.create_index(
            [
                ("anomaly.detector_id", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ]
        )
        await self.events.create_index("event_id", unique=True)
        await self.events.create_index([("incident_id", ASCENDING), ("timestamp", ASCENDING)])
        await self.events.create_index([("agent_role", ASCENDING), ("timestamp", DESCENDING)])
        await self.tasks.create_index("task_id", unique=True)
        await self.tasks.create_index("idempotency_key", unique=True)
        await self.tasks.create_index([("incident_id", ASCENDING), ("created_at", ASCENDING)])
        await self.tasks.create_index([("state", ASCENDING), ("updated_at", ASCENDING)])
        await self.tasks.create_index([("assigned_to", ASCENDING), ("state", ASCENDING)])

    async def ping(self) -> bool:
        try:
            await self.client.admin.command({"ping": 1})
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()

    async def _next_incident_id(self) -> str:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        counter = await self.counters.find_one_and_update(
            {"_id": f"incident:{day}"},
            {
                "$inc": {"sequence": 1},
                "$setOnInsert": {"day": day},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if counter is None:
            raise RuntimeError("MongoDB did not return the daily incident counter")
        return format_incident_id(day, int(counter["sequence"]))

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
                {"anomaly.detector_name": regex},
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

    async def find_active_incident_by_detector(
        self,
        detector_id: str,
    ) -> dict[str, Any] | None:
        document = await self.incidents.find_one(
            {
                "anomaly.detector_id": detector_id,
                "status": {"$in": sorted(ACTIVE_STATUSES)},
            },
            sort=[("updated_at", DESCENDING)],
        )
        return public_document(document)

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        document = await self.incidents.find_one({"incident_id": incident_id})
        return public_document(document)

    async def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested_id = str(payload.get("incident_id") or "").strip() or None
        incident_id = requested_id or await self._next_incident_id()
        incident = normalize_incident(payload, incident_id=incident_id)
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

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a durable task once for a stable idempotency key."""

        task = normalize_task(payload)
        existing = await self.tasks.find_one(
            {"idempotency_key": task["idempotency_key"]}
        )
        if existing is not None:
            return public_document(existing) or {}

        task["_id"] = task["task_id"]
        try:
            await self.tasks.insert_one(task)
        except DuplicateKeyError:
            # A concurrent retry may have inserted the same idempotent work item.
            existing = await self.tasks.find_one(
                {"idempotency_key": task["idempotency_key"]}
            )
            if existing is None:
                raise
            return public_document(existing) or {}
        return public_document(task) or {}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        document = await self.tasks.find_one({"task_id": task_id})
        return public_document(document)

    async def list_tasks(
        self,
        *,
        states: list[str] | None = None,
        incident_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if states:
            query["state"] = {"$in": [str(state).upper() for state in states]}
        if incident_id:
            query["incident_id"] = incident_id
        bounded_limit = min(max(limit, 1), 500)
        cursor = self.tasks.find(query).sort("updated_at", ASCENDING).limit(bounded_limit)
        documents: list[dict[str, Any]] = []
        async for document in cursor:
            documents.append(public_document(document) or {})
        return documents

    async def transition_task(
        self,
        task_id: str,
        *,
        expected_states: list[str],
        new_state: str,
        patch: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any] | None:
        """Compare-and-set a task state so concurrent workers cannot double-advance it."""

        normalized_expected = [str(state).upper() for state in expected_states]
        if not normalized_expected:
            raise ValueError("expected_states cannot be empty")
        target = str(new_state).upper()
        now = utc_now()

        changes = sanitize_task_payload(patch or {})
        for immutable in {
            "task_id",
            "incident_id",
            "task_type",
            "created_by",
            "assigned_to",
            "idempotency_key",
            "created_at",
            "attempt",
            "max_attempts",
        }:
            changes.pop(immutable, None)
        changes["state"] = target
        changes["updated_at"] = now
        if isinstance(changes.get("last_error"), dict):
            changes["last_error"].setdefault("at", now)
        if target in TERMINAL_TASK_STATES:
            changes["finished_at"] = now
        else:
            changes["finished_at"] = None

        update: dict[str, Any] = {"$set": changes}
        if increment_attempt:
            update["$inc"] = {"attempt": 1}

        document = await self.tasks.find_one_and_update(
            {
                "task_id": task_id,
                "state": {"$in": normalized_expected},
            },
            update,
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)
