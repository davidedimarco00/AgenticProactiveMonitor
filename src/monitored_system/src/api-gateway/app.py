from __future__ import annotations

import os
import time

import requests
from flask import Flask, abort, flash, redirect, render_template, request, url_for

from common.logging_utils import new_request_id, write_log

SERVICE_NAME = "api-gateway"
NOTES_SERVICE_URL = os.getenv("NOTES_SERVICE_URL", "http://processing-service:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "notes-platform-dev-secret")


def call_notes_service(
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> requests.Response:
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    start = time.perf_counter()

    try:
        response = requests.request(
            method,
            f"{NOTES_SERVICE_URL}{path}",
            headers={"X-Request-ID": request_id},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        write_log(
            service=SERVICE_NAME,
            level="ERROR",
            event_type="notes_service_unavailable",
            message="Unable to contact notes service",
            request_id=request_id,
            downstream="processing-service",
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
        )
        abort(503, description="Notes service is temporarily unavailable")

    latency_ms = round((time.perf_counter() - start) * 1000, 3)
    write_log(
        service=SERVICE_NAME,
        level="INFO" if response.status_code < 500 else "ERROR",
        event_type="downstream_request_completed",
        message="Notes service request completed",
        request_id=request_id,
        downstream="processing-service",
        method=method,
        path=path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )
    return response


def parse_or_abort(response: requests.Response):
    if response.status_code == 404:
        abort(404)
    if response.status_code >= 400:
        abort(response.status_code)
    if response.status_code == 204:
        return None
    return response.json()


@app.get("/health")
def health():
    response = call_notes_service("GET", "/health")
    parse_or_abort(response)
    return {"status": "ok", "service": SERVICE_NAME, "notes_service": "ok"}


@app.get("/")
def dashboard():
    response = call_notes_service("GET", "/notes")
    notes = parse_or_abort(response)
    return render_template("dashboard.html", notes=notes)


@app.route("/notes/new", methods=["GET", "POST"])
def create_note():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            flash("Title and content are required.", "error")
            return render_template("note_form.html", note=None, mode="create"), 400

        response = call_notes_service(
            "POST",
            "/notes",
            payload={"title": title, "content": content},
        )
        note = parse_or_abort(response)
        flash("Note created successfully.", "success")
        return redirect(url_for("note_detail", note_id=note["id"]))

    return render_template("note_form.html", note=None, mode="create")


@app.get("/notes/<int:note_id>")
def note_detail(note_id: int):
    response = call_notes_service("GET", f"/notes/{note_id}")
    note = parse_or_abort(response)
    return render_template("note_detail.html", note=note)


@app.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
def edit_note(note_id: int):
    if request.method == "GET":
        response = call_notes_service("GET", f"/notes/{note_id}")
        note = parse_or_abort(response)
        return render_template("note_form.html", note=note, mode="edit")

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("Title and content are required.", "error")
        return render_template(
            "note_form.html",
            note={"id": note_id, "title": title, "content": content},
            mode="edit",
        ), 400

    response = call_notes_service(
        "PUT",
        f"/notes/{note_id}",
        payload={"title": title, "content": content},
    )
    parse_or_abort(response)
    flash("Note updated successfully.", "success")
    return redirect(url_for("note_detail", note_id=note_id))


@app.post("/notes/<int:note_id>/delete")
def delete_note(note_id: int):
    response = call_notes_service("DELETE", f"/notes/{note_id}")
    parse_or_abort(response)
    flash("Note deleted.", "success")
    return redirect(url_for("dashboard"))


@app.errorhandler(404)
def not_found(_error):
    return render_template(
        "error.html",
        title="Note not found",
        message="The requested note does not exist.",
    ), 404


@app.errorhandler(503)
def service_unavailable(error):
    write_log(
        service=SERVICE_NAME,
        level="ERROR",
        event_type="request_failed",
        message="Web request failed because a downstream service was unavailable",
        path=request.path,
        status_code=503,
    )
    return render_template(
        "error.html",
        title="Service unavailable",
        message=getattr(error, "description", "The notes service is temporarily unavailable."),
    ), 503
