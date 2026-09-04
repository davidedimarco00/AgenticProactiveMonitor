from __future__ import annotations

from copy import deepcopy
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.server_api import ServerApi

from .mongodb import public_document, utc_now


ANOMALY_INBOX_STATES = {"WAITING", "RECOVERY", "PROCESSING", "COMPLETED", "DISMISSED"}
ANOMALY_OBSERVATION_FIELDS = {
    "result_id",
    "result_index",
    "detector_id",
    "detector_name",
    "detector_description",
    "detector_indices",
    "anomaly_grade",
    "confidence",
    "anomaly_score",
    "data_start_time",
    "data_end_time",
    "execution_start_time",
    "execution_end_time",
    "source",
}


def normalize_anomaly_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the durable representation of one normalized anomaly observation."""

    observation = {
        key: deepcopy(value)
        for key, value in payload.items()
        if key in ANOMALY_OBSERVATION_FIELDS
    }
    result_id = str(observation.get("result_id") or "").strip()
    result_index = str(observation.get("result_index") or "").strip()
    detector_id = str(observation.get("detector_id") or "").strip()
    if not result_id or not result_index or not detector_id:
        raise ValueError("Anomaly observation requires result_id, result_index and detector_id")

    detector_name = str(observation.get("detector_name") or "").strip() or None
    detector_description = str(observation.get("detector_description") or "").strip()
    detector_indices = observation.get("detector_indices") or []
    if not isinstance(detector_indices, (list, tuple)):
        detector_indices = []
    source = str(observation.get("source") or "opensearch").strip().lower() or "opensearch"

    observation["detector_name"] = detector_name
    observation["detector_description"] = detector_description
    observation["detector_indices"] = [str(index) for index in detector_indices]
    observation["source"] = source

    now = utc_now()
    anomaly_key = f"{result_index}:{result_id}"
    return {
        "anomaly_key": anomaly_key,
        **observation,
        "state": "WAITING",
        "incident_id": None,
        "processing_attempt": 0,
        "received_at": now,
        "processing_started_at": None,
        "completed_at": None,
        "dismissed_at": None,
        "dismissed_by": None,
        "dismissal_reason": None,
        "last_error": None,
        "updated_at": now,
    }


class MongoAnomalyInbox:
    """Durable FIFO inbox for normalized anomaly observations.

    MongoDB is the source of truth for anomaly admission. The in-memory asyncio
    queue is only the execution queue for the current backend process. Therefore
    WAITING observations remain inspectable and recoverable after a restart.
    Production observations use source=opensearch; explicit test injections use
    source=test_injector while following the same durable FIFO path.
    """

    def __init__(self, uri: str, database_name: str) -> None:
        self.client = AsyncMongoClient(uri, server_api=ServerApi("1"))
        self.database = self.client[database_name]
        self.collection = self.database["anomaly_inbox"]

    async def connect(self) -> None:
        await self.client.admin.command({"ping": 1})
        await self.collection.create_index("anomaly_key", unique=True)
        await self.collection.create_index([("state", ASCENDING), ("received_at", ASCENDING)])
        await self.collection.create_index([("incident_id", ASCENDING), ("state", ASCENDING)])
        await self.collection.create_index([("detector_id", ASCENDING), ("received_at", DESCENDING)])
        await self.collection.create_index([("detector_name", ASCENDING), ("received_at", DESCENDING)])

    async def close(self) -> None:
        await self.client.close()

    async def record_anomaly(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist one anomaly result exactly once before FIFO admission."""

        record = normalize_anomaly_record(payload)
        record["_id"] = record["anomaly_key"]
        try:
            await self.collection.insert_one(record)
            return public_document(record) or {}
        except DuplicateKeyError:
            existing = await self.collection.find_one({"anomaly_key": record["anomaly_key"]})
            if existing is None:
                raise
            return public_document(existing) or {}

    async def get_anomaly(self, anomaly_key: str) -> dict[str, Any] | None:
        document = await self.collection.find_one({"anomaly_key": anomaly_key})
        return public_document(document)

    async def update_detector_metadata(
        self,
        anomaly_key: str,
        detector_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist human-readable SINGLE_ENTITY detector metadata after admission."""

        name = str(detector_context.get("name") or "").strip()
        description = str(detector_context.get("description") or "").strip()
        indices = detector_context.get("indices") or []
        if not isinstance(indices, list):
            indices = []

        now = utc_now()
        document = await self.collection.find_one_and_update(
            {"anomaly_key": anomaly_key},
            {
                "$set": {
                    "detector_name": name or None,
                    "detector_description": description,
                    "detector_indices": [str(index) for index in indices],
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)

    async def list_anomalies(
        self,
        *,
        states: list[str] | None = None,
        incident_id: str | None = None,
        limit: int = 200,
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if states:
            normalized = [str(state).strip().upper() for state in states]
            invalid = [state for state in normalized if state not in ANOMALY_INBOX_STATES]
            if invalid:
                raise ValueError(f"Unsupported anomaly inbox state(s): {invalid}")
            query["state"] = {"$in": normalized}
        if incident_id:
            query["incident_id"] = incident_id

        bounded_limit = min(max(int(limit), 1), 4096)
        direction = ASCENDING if ascending else DESCENDING
        cursor = self.collection.find(query).sort("received_at", direction).limit(bounded_limit)
        documents: list[dict[str, Any]] = []
        async for document in cursor:
            documents.append(public_document(document) or {})
        return documents

    async def count_anomalies(self, *, states: list[str] | None = None) -> int:
        query: dict[str, Any] = {}
        if states:
            normalized = [str(state).strip().upper() for state in states]
            invalid = [state for state in normalized if state not in ANOMALY_INBOX_STATES]
            if invalid:
                raise ValueError(f"Unsupported anomaly inbox state(s): {invalid}")
            query["state"] = {"$in": normalized}
        return int(await self.collection.count_documents(query))

    async def mark_anomaly_processing(self, anomaly_key: str) -> dict[str, Any] | None:
        now = utc_now()
        document = await self.collection.find_one_and_update(
            {"anomaly_key": anomaly_key, "state": {"$in": ["WAITING", "RECOVERY"]}},
            {
                "$set": {
                    "state": "PROCESSING",
                    "processing_started_at": now,
                    "last_error": None,
                    "updated_at": now,
                },
                "$inc": {"processing_attempt": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            document = await self.collection.find_one(
                {"anomaly_key": anomaly_key, "state": "PROCESSING"}
            )
        return public_document(document)

    async def mark_anomaly_completed(self, anomaly_key: str) -> dict[str, Any] | None:
        now = utc_now()
        document = await self.collection.find_one_and_update(
            {"anomaly_key": anomaly_key, "state": {"$ne": "DISMISSED"}},
            {
                "$set": {
                    "state": "COMPLETED",
                    "completed_at": now,
                    "processing_started_at": None,
                    "last_error": None,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)

    async def mark_anomaly_retryable(
        self,
        anomaly_key: str,
        *,
        error: str,
    ) -> dict[str, Any] | None:
        now = utc_now()
        document = await self.collection.find_one_and_update(
            {
                "anomaly_key": anomaly_key,
                "state": {"$nin": ["COMPLETED", "DISMISSED"]},
            },
            {
                "$set": {
                    "state": "WAITING",
                    "processing_started_at": None,
                    "last_error": str(error),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)

    async def dismiss_waiting_anomaly(
        self,
        anomaly_key: str,
        *,
        dismissed_by: str = "operator",
        reason: str = "Marked as not a true anomaly by the operator.",
    ) -> dict[str, Any] | None:
        """Soft-remove an unowned WAITING anomaly from future FIFO processing."""

        now = utc_now()
        document = await self.collection.find_one_and_update(
            {
                "anomaly_key": anomaly_key,
                "state": "WAITING",
                "incident_id": None,
            },
            {
                "$set": {
                    "state": "DISMISSED",
                    "dismissed_at": now,
                    "dismissed_by": str(dismissed_by or "operator"),
                    "dismissal_reason": str(reason),
                    "processing_started_at": None,
                    "last_error": None,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)

    async def link_anomaly_to_incident(
        self,
        anomaly_key: str,
        incident_id: str,
    ) -> dict[str, Any] | None:
        now = utc_now()
        document = await self.collection.find_one_and_update(
            {"anomaly_key": anomaly_key, "state": {"$ne": "DISMISSED"}},
            {"$set": {"incident_id": incident_id, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)

    async def release_orphaned_incident_link(self, anomaly_key: str) -> dict[str, Any] | None:
        """Return an inbox item to normal FIFO ownership if its incident vanished."""

        now = utc_now()
        document = await self.collection.find_one_and_update(
            {
                "anomaly_key": anomaly_key,
                "state": {"$nin": ["COMPLETED", "DISMISSED"]},
            },
            {
                "$set": {
                    "incident_id": None,
                    "state": "WAITING",
                    "processing_started_at": None,
                    "last_error": "linked incident was not found during backend recovery",
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return public_document(document)

    async def mark_incident_anomalies_recovery_queued(self, incident_id: str) -> int:
        """Reserve linked observations while a synthetic incident recovery is queued."""

        now = utc_now()
        result = await self.collection.update_many(
            {"incident_id": incident_id, "state": "WAITING"},
            {
                "$set": {
                    "state": "RECOVERY",
                    "processing_started_at": None,
                    "updated_at": now,
                }
            },
        )
        return int(result.modified_count)

    async def mark_incident_anomalies_processing(self, incident_id: str) -> int:
        now = utc_now()
        result = await self.collection.update_many(
            {"incident_id": incident_id, "state": {"$in": ["WAITING", "RECOVERY"]}},
            {
                "$set": {
                    "state": "PROCESSING",
                    "processing_started_at": now,
                    "updated_at": now,
                },
                "$inc": {"processing_attempt": 1},
            },
        )
        return int(result.modified_count)

    async def mark_incident_anomalies_completed(self, incident_id: str) -> int:
        now = utc_now()
        result = await self.collection.update_many(
            {
                "incident_id": incident_id,
                "state": {"$nin": ["COMPLETED", "DISMISSED"]},
            },
            {
                "$set": {
                    "state": "COMPLETED",
                    "completed_at": now,
                    "processing_started_at": None,
                    "last_error": None,
                    "updated_at": now,
                }
            },
        )
        return int(result.modified_count)

    async def recover_interrupted_processing(self) -> dict[str, int]:
        """Move volatile PROCESSING/RECOVERY observations back to WAITING on startup."""

        interrupted = await self.collection.count_documents(
            {"state": {"$in": ["PROCESSING", "RECOVERY"]}}
        )
        if interrupted:
            now = utc_now()
            result = await self.collection.update_many(
                {"state": {"$in": ["PROCESSING", "RECOVERY"]}},
                {
                    "$set": {
                        "state": "WAITING",
                        "processing_started_at": None,
                        "last_error": "backend restart interrupted anomaly processing",
                        "updated_at": now,
                    }
                },
            )
            reset = int(result.modified_count)
        else:
            reset = 0

        waiting = await self.collection.count_documents({"state": "WAITING"})
        return {
            "interrupted": int(interrupted),
            "reset_to_waiting": reset,
            "waiting": int(waiting),
        }
