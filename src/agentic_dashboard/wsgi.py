from __future__ import annotations

from io import BytesIO

from flask import jsonify, render_template, send_file

from app import APP_NAME, app, get_incident, search_anomalies
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


# app.py already registers /incidents/<incident_id>/report.pdf. Replacing the endpoint
# keeps the public dashboard URL unchanged while rendering the report from the incident
# JSON that the dashboard already consumes. This avoids oversized backend timeline rows.
app.view_functions["incident_report"] = _dashboard_incident_report

# The dashboard container starts through this module, so dashboard-only pages can be
# registered here without changing the autonomous backend or its public API contract.
app.add_url_rule("/anomalies", endpoint="anomalies_page", view_func=anomalies_page, methods=["GET"])

__all__ = ["app"]
