from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _text(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value))


def _confidence(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return _text(value)
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:.0f}%"


def build_incident_report(incident: dict[str, Any], events: list[dict[str, Any]]) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Incident Report {incident.get('incident_id', '')}",
        author="Agentic Proactive Monitor",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER, spaceAfter=10))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8.5, leading=11))

    story: list[Any] = [
        Paragraph("Agentic Proactive Monitor", styles["ReportTitle"]),
        Paragraph(f"Incident Analysis Report - {_text(incident.get('incident_id'))}", styles["Heading1"]),
        Spacer(1, 4 * mm),
    ]

    metadata = [
        ["Status", _text(incident.get("status")), "Severity", _text(incident.get("severity"))],
        ["Affected component", _text(incident.get("entity") or incident.get("service")), "Detected", _text(incident.get("detected_at") or incident.get("created_at"))],
        ["Last update", _text(incident.get("updated_at")), "Closed", _text(incident.get("closed_at"))],
    ]
    table = Table(metadata, colWidths=[34 * mm, 55 * mm, 28 * mm, 55 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([table, Spacer(1, 4 * mm)])

    anomaly = incident.get("anomaly") or {}
    story.extend([
        Paragraph("1. Incident summary", styles["Section"]),
        Paragraph(_text(incident.get("takeover_reason"), "The incident was created from an OpenSearch anomaly event."), styles["BodyText"]),
        Paragraph("2. Anomaly", styles["Section"]),
        Paragraph(
            f"Detector: <b>{_text(anomaly.get('detector_id'))}</b><br/>"
            f"Type: <b>{_text(anomaly.get('anomaly_type'))}</b><br/>"
            f"Grade: <b>{_text(anomaly.get('grade'))}</b><br/>"
            f"Detector confidence: <b>{_confidence(anomaly.get('confidence'))}</b>",
            styles["BodyText"],
        ),
    ])

    diagnosis = incident.get("diagnosis") or {}
    story.extend([
        Paragraph("3. Agentic diagnosis", styles["Section"]),
        Paragraph(f"<b>Diagnosis:</b> {_text(diagnosis.get('summary'), 'Not available yet.')}", styles["BodyText"]),
        Paragraph(f"<b>Possible root cause:</b> {_text(diagnosis.get('root_cause'))}", styles["BodyText"]),
        Paragraph(f"<b>Confidence:</b> {_confidence(diagnosis.get('confidence'))}", styles["BodyText"]),
    ])

    remediation = incident.get("remediation") or {}
    story.extend([
        Paragraph("4. Recommended remediation", styles["Section"]),
        Paragraph(_text(remediation.get("summary"), "No remediation recommendation has been produced yet."), styles["BodyText"]),
    ])
    for index, step in enumerate(remediation.get("steps") or [], start=1):
        if isinstance(step, dict):
            label = step.get("title") or step.get("action") or "Operator action"
            detail = step.get("description") or step.get("command") or ""
            line = f"<b>{index}. {_text(label)}</b> {_text(detail, '')}"
        else:
            line = f"<b>{index}.</b> {_text(step)}"
        story.append(Paragraph(line, styles["BodyText"]))

    validation = incident.get("validation") or {}
    story.extend([
        Paragraph("5. Validation and final outcome", styles["Section"]),
        Paragraph(f"<b>Validation status:</b> {_text(validation.get('status'))}", styles["BodyText"]),
        Paragraph(f"<b>Validation summary:</b> {_text(validation.get('summary'))}", styles["BodyText"]),
        Paragraph(f"<b>Incident outcome:</b> {_text(incident.get('status'))}", styles["BodyText"]),
        Paragraph("6. Agent activity timeline", styles["Section"]),
    ])

    if events:
        event_rows: list[list[Any]] = [["Time", "Agent", "Event", "Description / outcome"]]
        for event in events:
            description = event.get("description") or event.get("outcome") or event.get("reason") or "-"
            event_rows.append([
                Paragraph(_text(event.get("timestamp")), styles["Small"]),
                Paragraph(_text(event.get("agent_role") or event.get("agent_jid")), styles["Small"]),
                Paragraph(_text(event.get("action") or event.get("event_type")), styles["Small"]),
                Paragraph(_text(description), styles["Small"]),
            ])
        events_table = Table(event_rows, colWidths=[37 * mm, 32 * mm, 40 * mm, 63 * mm], repeatRows=1)
        events_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(events_table)
    else:
        story.append(Paragraph("No structured agent activity has been recorded yet.", styles["BodyText"]))

    story.extend([
        Spacer(1, 6 * mm),
        Paragraph(
            "This report contains the incident conclusions and structured agent activity. Raw metrics and logs remain available in OpenSearch and are not duplicated in the agentic dashboard.",
            styles["Small"],
        ),
    ])

    document.build(story)
    return buffer.getvalue()
