from __future__ import annotations

from io import BytesIO

from flask import jsonify, render_template, send_file

from app import APP_NAME, app, get_incident
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


# app.py already registers /incidents/<incident_id>/report.pdf. Replacing the endpoint
# keeps the public dashboard URL unchanged while rendering the report from the incident
# JSON that the dashboard already consumes. This avoids oversized backend timeline rows.
app.view_functions["incident_report"] = _dashboard_incident_report

__all__ = ["app"]
