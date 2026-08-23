from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.logging_utils import new_request_id, write_exception, write_log

SERVICE_NAME = "processing-service"
DATA_SERVICE_URL = os.getenv("DATA_SERVICE_URL", "http://data-service:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))
FAULT_DELAY_FILE = Path(
    os.getenv("FAULT_DELAY_FILE", "/var/run/monitored-faults/processing-delay-ms")
)
MAX_FAULT_DELAY_MS = int(os.getenv("MAX_FAULT_DELAY_MS", "30000"))


class NoteWrite(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=10000)


def level_for_status(status_code: int) -> str:
    if status_code >= 500:
        return "ERROR"
    if status_code >= 400:
        return "WARN"
    return "INFO"


def configured_fault_delay_ms() -> int:
    if not FAULT_DELAY_FILE.exists():
        return 0

    try:
        delay_ms = int(FAULT_DELAY_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0

    return max(0, min(delay_ms, MAX_FAULT_DELAY_MS))


async def apply_fault_delay(request_id: str) -> None:
    delay_ms = configured_fault_delay_ms()
    if delay_ms <= 0:
        return

    write_log(
        service=SERVICE_NAME,
        level="WARN",
        event_type="fault_delay_applied",
        message="Controlled processing delay applied",
        request_id=request_id,
        fault_type="high_latency",
        delay_ms=delay_ms,
    )
    await asyncio.sleep(delay_ms / 1000)


async def forward_request(
    method: str,
    path: str,
    request_id: str,
    payload: dict[str, object] | None = None,
) -> httpx.Response:
    await apply_fault_delay(request_id)

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method,
                f"{DATA_SERVICE_URL}{path}",
                headers={"X-Request-ID": request_id},
                json=payload,
            )
    except httpx.RequestError as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        write_exception(
            service=SERVICE_NAME,
            event_type="data_service_unavailable",
            message="Unable to contact data service",
            exception=exc,
            request_id=request_id,
            downstream="data-service",
            latency_ms=latency_ms,
        )
        raise HTTPException(status_code=503, detail="data service unavailable") from exc

    latency_ms = round((time.perf_counter() - start) * 1000, 3)
    write_log(
        service=SERVICE_NAME,
        level=level_for_status(response.status_code),
        event_type="downstream_request_completed",
        message="Data service request completed",
        request_id=request_id,
        downstream="data-service",
        method=method,
        path=path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )
    return response


def request_id_from(request: Request) -> str:
    return request.headers.get("X-Request-ID") or new_request_id()


def raise_for_downstream(response: httpx.Response) -> None:
    if response.status_code >= 400:
        detail = "downstream request failed"
        try:
            body = response.json()
            detail = body.get("detail", detail)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)


app = FastAPI(title="Notes Service", version="0.2.0")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = request_id_from(request)
    errors = exc.errors()
    write_log(
        service=SERVICE_NAME,
        level="WARN",
        event_type="request_validation_failed",
        message="Processing service request validation failed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=422,
        validation_errors=errors,
    )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(errors)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = request_id_from(request)
    write_log(
        service=SERVICE_NAME,
        level=level_for_status(exc.status_code),
        event_type="request_failed",
        message="Processing service request failed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=exc.status_code,
        error_type=type(exc).__name__,
        error_message=str(exc.detail),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_from(request)
    write_exception(
        service=SERVICE_NAME,
        event_type="unhandled_exception",
        message="Unhandled exception while processing request",
        exception=exc,
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=500,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error"},
    )


@app.on_event("startup")
def on_startup() -> None:
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="service_started",
        message="Notes processing service started",
        data_service_url=DATA_SERVICE_URL,
        fault_delay_file=str(FAULT_DELAY_FILE),
    )


@app.on_event("shutdown")
def on_shutdown() -> None:
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="service_stopped",
        message="Notes processing service stopped",
    )


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    request_id = request_id_from(request)
    response = await forward_request("GET", "/health", request_id)
    raise_for_downstream(response)
    return {"status": "ok", "service": SERVICE_NAME, "data_service": "ok"}


@app.get("/notes")
async def list_notes(request: Request) -> list[dict[str, object]]:
    request_id = request_id_from(request)
    response = await forward_request("GET", "/notes", request_id)
    raise_for_downstream(response)
    notes = response.json()
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="notes_listed",
        message="Notes returned to caller",
        request_id=request_id,
        note_count=len(notes),
    )
    return notes


@app.get("/notes/{note_id}")
async def get_note(note_id: int, request: Request) -> dict[str, object]:
    request_id = request_id_from(request)
    response = await forward_request("GET", f"/notes/{note_id}", request_id)
    raise_for_downstream(response)
    return response.json()


@app.post("/notes", status_code=status.HTTP_201_CREATED)
async def create_note(payload: NoteWrite, request: Request) -> dict[str, object]:
    request_id = request_id_from(request)
    response = await forward_request("POST", "/notes", request_id, payload.model_dump())
    raise_for_downstream(response)
    note = response.json()
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_created",
        message="Note creation completed",
        request_id=request_id,
        note_id=note.get("id"),
    )
    return note


@app.put("/notes/{note_id}")
async def update_note(note_id: int, payload: NoteWrite, request: Request) -> dict[str, object]:
    request_id = request_id_from(request)
    response = await forward_request(
        "PUT", f"/notes/{note_id}", request_id, payload.model_dump()
    )
    raise_for_downstream(response)
    note = response.json()
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_updated",
        message="Note update completed",
        request_id=request_id,
        note_id=note_id,
    )
    return note


@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: int, request: Request) -> Response:
    request_id = request_id_from(request)
    response = await forward_request("DELETE", f"/notes/{note_id}", request_id)
    raise_for_downstream(response)
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_deleted",
        message="Note deletion completed",
        request_id=request_id,
        note_id=note_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
