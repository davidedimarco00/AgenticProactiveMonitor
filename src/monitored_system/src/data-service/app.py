from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.logging_utils import new_request_id, write_exception, write_log

SERVICE_NAME = "data-service"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/var/lib/notes/notes.db"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def level_for_status(status_code: int) -> str:
    if status_code >= 500:
        return "ERROR"
    if status_code >= 400:
        return "WARN"
    return "INFO"


def safe_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    return [
        {
            "type": error.get("type"),
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg"),
        }
        for error in exc.errors()
    ]


def request_id_from(request: Request) -> str:
    return request.headers.get("X-Request-ID") or new_request_id()


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


class NoteWrite(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=10000)


class Note(BaseModel):
    id: int
    title: str
    content: str
    created_at: str
    updated_at: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="service_started",
        message="Data service started",
        database_path=str(DATABASE_PATH),
    )
    yield
    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="service_stopped",
        message="Data service stopped",
    )


app = FastAPI(title="Notes Data Service", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = request_id_from(request)
    errors = exc.errors()
    safe_errors = safe_validation_errors(exc)
    write_log(
        service=SERVICE_NAME,
        level="WARN",
        event_type="request_validation_failed",
        message="Data service request validation failed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=422,
        validation_errors=safe_errors,
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
        message="Data service request failed",
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
        message="Unhandled exception while processing data request",
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


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "service": SERVICE_NAME, "database": "sqlite"}
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@app.get("/notes", response_model=list[Note])
def list_notes(request: Request) -> list[Note]:
    request_id = request_id_from(request)
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes ORDER BY updated_at DESC"
        ).fetchall()

    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="notes_listed",
        message="Notes retrieved from database",
        request_id=request_id,
        note_count=len(rows),
    )
    return [Note(**dict(row)) for row in rows]


@app.get("/notes/{note_id}", response_model=Note)
def get_note(note_id: int, request: Request) -> Note:
    request_id = request_id_from(request)
    with connect() as connection:
        row = connection.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()

    if row is None:
        write_log(
            service=SERVICE_NAME,
            level="WARN",
            event_type="note_not_found",
            message="Requested note was not found",
            request_id=request_id,
            note_id=note_id,
        )
        raise HTTPException(status_code=404, detail="note not found")

    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_read",
        message="Note retrieved from database",
        request_id=request_id,
        note_id=note_id,
    )
    return Note(**dict(row))


@app.post("/notes", response_model=Note, status_code=status.HTTP_201_CREATED)
def create_note(payload: NoteWrite, request: Request) -> Note:
    request_id = request_id_from(request)
    timestamp = now_iso()

    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (payload.title, payload.content, timestamp, timestamp),
        )
        note_id = cursor.lastrowid
        connection.commit()
        row = connection.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()

    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_created",
        message="Note persisted in database",
        request_id=request_id,
        note_id=note_id,
        title_length=len(payload.title),
        content_length=len(payload.content),
    )
    return Note(**dict(row))


@app.put("/notes/{note_id}", response_model=Note)
def update_note(note_id: int, payload: NoteWrite, request: Request) -> Note:
    request_id = request_id_from(request)
    timestamp = now_iso()

    with connect() as connection:
        existing = connection.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="note not found")

        connection.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
            (payload.title, payload.content, timestamp, note_id),
        )
        connection.commit()
        row = connection.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()

    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_updated",
        message="Note updated in database",
        request_id=request_id,
        note_id=note_id,
    )
    return Note(**dict(row))


@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, request: Request) -> Response:
    request_id = request_id_from(request)

    with connect() as connection:
        cursor = connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        connection.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="note not found")

    write_log(
        service=SERVICE_NAME,
        level="INFO",
        event_type="note_deleted",
        message="Note deleted from database",
        request_id=request_id,
        note_id=note_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
