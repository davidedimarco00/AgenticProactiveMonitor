from __future__ import annotations

import os
from io import BytesIO

import requests
from flask import jsonify, render_template, send_file

from app import (
    APP_NAME,
    OLLAMA_URL,
    REQUEST_TIMEOUT,
    app,
    get_incident,
    search_anomalies,
    utc_now,
)
from reporting import build_dashboard_incident_report


def _dashboard_incident_report(incident_id: str):
    incident = get_incident(incident_id)
    if incident is None:
        return render_template("not_found.html", app_name=APP_NAME, incident_id=incident_id), 404

    try:
        content = build_dashboard_incident_report(incident)
    except Exception as exc:
        app.logger.exception(
            "Dashboard incident report generation failed for %s: %s",
            incident_id,
            exc,
        )
        return jsonify({"error": "Incident report could not be generated"}), 500

    return send_file(
        BytesIO(content),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{incident_id}-incident-report.pdf",
        max_age=0,
    )


def anomalies_page():
    snapshot = search_anomalies(limit=1, state="WAITING", ascending=True)
    return render_template(
        "anomalies.html",
        app_name=APP_NAME,
        anomaly_summary=snapshot.get("summary") or {},
    )


def _configured_ollama_models() -> list[dict[str, str]]:
    """Models configured by the current project defaults.

    Environment overrides are honored when they are explicitly provided to the
    dashboard container. The fallbacks mirror the agentic backend and RAG
    configuration in docker-compose.yml.
    """
    return [
        {
            "role": "Reasoning",
            "name": os.getenv("OLLAMA_REASONING_MODEL")
            or os.getenv("OLLAMA_CHAT_MODEL")
            or "gemma4:e2b",
        },
        {
            "role": "Tool calling",
            "name": os.getenv("OLLAMA_TOOL_MODEL") or "qwen3.5:4b",
        },
        {
            "role": "Tool fallback",
            "name": os.getenv("OLLAMA_FALLBACK_TOOL_MODEL") or "qwen2.5:latest",
        },
        {
            "role": "Embeddings",
            "name": os.getenv("OLLAMA_EMBEDDING_MODEL") or "ibm/granite-embedding:30m",
        },
    ]


def _ollama_model_name(raw: dict[str, object]) -> str:
    return str(raw.get("name") or raw.get("model") or "").strip()


def ollama_loaded_models():
    """Return configured models plus Ollama's current in-memory state."""
    try:
        ps_response = requests.get(f"{OLLAMA_URL}/api/ps", timeout=REQUEST_TIMEOUT)
        ps_response.raise_for_status()
        ps_payload = ps_response.json()

        tags_response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=REQUEST_TIMEOUT)
        tags_response.raise_for_status()
        tags_payload = tags_response.json()
    except (requests.RequestException, ValueError) as exc:
        app.logger.warning("Could not query Ollama model state: %s", exc)
        configured = [
            {**model, "loaded": False, "available": None}
            for model in _configured_ollama_models()
        ]
        return (
            jsonify(
                {
                    "generated_at": utc_now(),
                    "status": "offline",
                    "count": 0,
                    "models": [],
                    "configured_models": configured,
                }
            ),
            503,
        )

    loaded_models: list[dict[str, object]] = []
    loaded_names: set[str] = set()
    for raw in ps_payload.get("models", []) if isinstance(ps_payload, dict) else []:
        if not isinstance(raw, dict):
            continue
        name = _ollama_model_name(raw)
        if not name:
            continue
        loaded_names.add(name)
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        loaded_models.append(
            {
                "name": name,
                "model": raw.get("model") or name,
                "size": raw.get("size"),
                "size_vram": raw.get("size_vram"),
                "expires_at": raw.get("expires_at"),
                "parameter_size": details.get("parameter_size"),
                "quantization_level": details.get("quantization_level"),
            }
        )

    available_names: set[str] = set()
    for raw in tags_payload.get("models", []) if isinstance(tags_payload, dict) else []:
        if isinstance(raw, dict):
            name = _ollama_model_name(raw)
            if name:
                available_names.add(name)

    configured_models = []
    for model in _configured_ollama_models():
        name = model["name"]
        configured_models.append(
            {
                **model,
                "loaded": name in loaded_names,
                "available": name in available_names,
            }
        )

    return jsonify(
        {
            "generated_at": utc_now(),
            "status": "online",
            "count": len(loaded_models),
            "models": loaded_models,
            "configured_models": configured_models,
        }
    )


# app.py already registers /incidents/<incident_id>/report.pdf. Replacing the endpoint
# keeps the public dashboard URL unchanged while rendering the report from the incident
# JSON that the dashboard already consumes. This avoids oversized backend timeline rows.
app.view_functions["incident_report"] = _dashboard_incident_report

# The dashboard container starts through this module, so dashboard-only pages can be
# registered here without changing the autonomous backend or its public API contract.
app.add_url_rule("/anomalies", endpoint="anomalies_page", view_func=anomalies_page, methods=["GET"])
app.add_url_rule(
    "/api/ollama-loaded-models",
    endpoint="ollama_loaded_models",
    view_func=ollama_loaded_models,
    methods=["GET"],
)

__all__ = ["app"]
