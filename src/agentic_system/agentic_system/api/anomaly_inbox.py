from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from ..integrations import ANOMALY_INBOX_STATES, MongoAnomalyInbox


OPERATOR_DISMISSAL_REASON = "Marked as not a true anomaly by the operator."
DASHBOARD_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _dismissal_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers={"Access-Control-Allow-Origin": "*"},
    )


def attach_anomaly_inbox_api(app: FastAPI, inbox: MongoAnomalyInbox) -> None:
    """Attach operator endpoints for durable anomaly-inbox observability and triage."""

    @app.get("/api/v1/anomalies", tags=["Anomalies"])
    async def list_anomalies(
        limit: int = Query(100, ge=1, le=4096),
        state: str | None = Query(None),
        ascending: bool = Query(True),
    ) -> dict[str, object]:
        states = None
        if state:
            normalized = state.strip().upper()
            if normalized not in ANOMALY_INBOX_STATES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported anomaly state {state!r}; expected one of "
                        + ", ".join(sorted(ANOMALY_INBOX_STATES))
                    ),
                )
            states = [normalized]

        anomalies = await inbox.list_anomalies(
            states=states,
            limit=limit,
            ascending=ascending,
        )
        waiting, recovery, processing, completed, dismissed = await asyncio.gather(
            inbox.count_anomalies(states=["WAITING"]),
            inbox.count_anomalies(states=["RECOVERY"]),
            inbox.count_anomalies(states=["PROCESSING"]),
            inbox.count_anomalies(states=["COMPLETED"]),
            inbox.count_anomalies(states=["DISMISSED"]),
        )
        return {
            "anomalies": anomalies,
            "summary": {
                "waiting": waiting,
                "recovery": recovery,
                "processing": processing,
                "completed": completed,
                "dismissed": dismissed,
            },
        }

    @app.options("/api/v1/anomalies/{anomaly_key:path}", include_in_schema=False)
    async def anomaly_dismissal_preflight(anomaly_key: str) -> Response:
        del anomaly_key
        return Response(status_code=204, headers=DASHBOARD_CORS_HEADERS)

    @app.delete("/api/v1/anomalies/{anomaly_key:path}", tags=["Anomalies"])
    async def dismiss_waiting_anomaly(anomaly_key: str) -> JSONResponse:
        """Remove one false-positive WAITING observation from future FIFO processing."""

        key = anomaly_key.strip()
        if not key:
            return _dismissal_error(400, "anomaly_key cannot be empty")

        current = await inbox.get_anomaly(key)
        if current is None:
            return _dismissal_error(404, "Anomaly not found")

        state = str(current.get("state") or "").upper()
        incident_id = str(current.get("incident_id") or "").strip()
        if state != "WAITING" or incident_id:
            detail = (
                "Only unowned WAITING anomalies can be dismissed. "
                f"Current state is {state or 'UNKNOWN'}"
                + (f" and incident {incident_id} already owns it." if incident_id else ".")
            )
            return _dismissal_error(409, detail)

        dismissed = await inbox.dismiss_waiting_anomaly(
            key,
            dismissed_by="operator",
            reason=OPERATOR_DISMISSAL_REASON,
        )
        if dismissed is None:
            # State may have changed between the read and atomic update.
            return _dismissal_error(
                409,
                "The anomaly is no longer available in WAITING state.",
            )

        return JSONResponse(
            {
                "status": "dismissed",
                "message": "Anomaly removed from the waiting queue as an operator false positive.",
                "anomaly": dismissed,
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )
