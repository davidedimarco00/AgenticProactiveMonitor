from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from common.logging_utils import new_request_id, write_log

SERVICE_NAME = "data-service"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/var/lib/notes/notes.db"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    request_id = request.headers.get("X-Request-ID") or new_request_id()
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
    request_id = request.headers.get("X-Request-ID") or new_request_id()
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
    request_id = request.headers.get("X-Request-ID") or new_request_id()
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
    request_id = request.headers.get("X-Request-ID") or new_request_id()
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
    request_id = request.headers.get("X-Request-ID") or new_request_id()

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
