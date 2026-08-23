from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


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


def _short_text(value: Any, limit: int = 1800) -> str:
    if value is None or value == "":
        return "-"
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _event_payload(event: dict[str, Any]) -> Any:
    return event.get("description") or event.get("outcome") or event.get("reason")


def _event_summary(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("summary", "message", "reason", "root_cause", "diagnosis"):
            value = payload.get(key)
            if value:
                return _short_text(value)
        return "Structured result produced by the agent. Full raw payload is available in the dashboard."
    if isinstance(payload, (list, tuple)):
        preview = "; ".join(str(item) for item in payload[:6])
        if len(payload) > 6:
            preview += f"; ... ({len(payload)} items total)"
        return _short_text(preview)
    return _short_text(payload)


def _event_facts(payload: Any) -> list[tuple[str, str]]:
    if not isinstance(payload, dict):
        return []
    facts: list[tuple[str, str]] = []
    status = payload.get("status")
    diagnosis_status = payload.get("diagnosis_status")
    confidence = payload.get("confidence")
    causal_chain = payload.get("causal_chain")
    root_cause = payload.get("root_cause")
    if status:
        facts.append(("Status", _short_text(status, 120)))
    if diagnosis_status:
        facts.append(("Diagnosis", _short_text(diagnosis_status, 120)))
    if confidence is not None and confidence != "":
        facts.append(("Confidence", _confidence(confidence)))
    if causal_chain:
        facts.append(("Causal chain", _short_text(causal_chain, 500)))
    if root_cause:
        facts.append(("Root cause", _short_text(root_cause, 900)))
    return facts


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(18 * mm, 10 * mm, "Agentic Proactive Monitor - Operator incident report")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {document.page}")
    canvas.restoreState()


def build_dashboard_incident_report(incident: dict[str, Any]) -> bytes:
    """Build a robust operator PDF from the incident JSON already exposed to the dashboard.

    Large structured agent payloads are summarized instead of being placed in a single PDF
    table row. This keeps the report readable and avoids ReportLab layout failures for very
    large ReAct results.
    """

    buffer = BytesIO()
    incident_id = str(incident.get("incident_id") or "incident")
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title=f"Incident Report {incident_id}",
        author="Agentic Proactive Monitor",
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#172033"),
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubtitle",
            parent=styles["BodyText"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#64748B"),
            fontSize=9,
            leading=12,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            textColor=colors.HexColor("#172033"),
            fontSize=14,
            leading=17,
            spaceBefore=10,
            spaceAfter=7,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#334155"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Muted",
            parent=styles["Small"],
            textColor=colors.HexColor("#64748B"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Command",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=7.7,
            leading=10,
            leftIndent=5,
            rightIndent=5,
            spaceBefore=4,
            spaceAfter=5,
            backColor=colors.HexColor("#F1F5F9"),
            borderColor=colors.HexColor("#CBD5E1"),
            borderWidth=0.4,
            borderPadding=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="EventAction",
            parent=styles["BodyText"],
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#172033"),
            fontName="Helvetica-Bold",
            spaceAfter=3,
        )
    )

    story: list[Any] = [
        Paragraph("Agentic Proactive Monitor", styles["ReportTitle"]),
        Paragraph(f"Incident Analysis Report - {_text(incident_id)}", styles["Heading1"]),
        Paragraph(
            "Operator-facing summary of anomaly context, autonomous diagnosis, remediation guidance, validation and structured agent activity.",
            styles["ReportSubtitle"],
        ),
    ]

    metadata = [
        ["Status", _text(incident.get("status")), "Severity", _text(incident.get("severity"))],
        [
            "Affected component",
            _text(incident.get("entity") or incident.get("service")),
            "Detected",
            _text(incident.get("detected_at") or incident.get("created_at")),
        ],
        ["Last update", _text(incident.get("updated_at")), "Closed", _text(incident.get("closed_at"))],
    ]
    metadata_table = Table(metadata, colWidths=[31 * mm, 58 * mm, 25 * mm, 58 * mm])
    metadata_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8E1EB")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.3),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#334155")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([metadata_table, Spacer(1, 3 * mm)])

    story.append(Paragraph("1. Incident context", styles["Section"]))
    story.append(
        Paragraph(
            _text(
                incident.get("takeover_reason"),
                "The incident was created after an anomaly was detected by OpenSearch.",
            ),
            styles["BodyText"],
        )
    )
    for factor in incident.get("takeover_factors") or []:
        story.append(Paragraph(f"- {_text(factor)}", styles["Small"]))

    anomaly = incident.get("anomaly") or {}
    anomaly_rows = [
        ["Detector", _text(anomaly.get("detector_name") or anomaly.get("detector_id"))],
        ["Type", _text(anomaly.get("anomaly_type"))],
        ["Grade", _text(anomaly.get("grade"))],
        ["AD confidence", _confidence(anomaly.get("confidence"))],
        ["Entity", _text(incident.get("entity") or incident.get("service"))],
    ]
    story.append(Paragraph("2. OpenSearch anomaly", styles["Section"]))
    anomaly_table = Table(anomaly_rows, colWidths=[42 * mm, 130 * mm])
    anomaly_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(anomaly_table)
    story.append(Paragraph("Single-entity detector", styles["Muted"]))

    diagnosis = incident.get("diagnosis") or {}
    agentic = incident.get("agentic") or {}
    story.extend(
        [
            Paragraph("3. Agentic diagnosis", styles["Section"]),
            Paragraph(f"<b>Diagnosis:</b> {_text(diagnosis.get('summary'), 'Diagnosis is still in progress.')}", styles["BodyText"]),
            Spacer(1, 1.5 * mm),
            Paragraph(f"<b>Possible root cause:</b> {_text(diagnosis.get('root_cause'))}", styles["BodyText"]),
            Paragraph(f"<b>Diagnostic confidence:</b> {_confidence(diagnosis.get('confidence'))}", styles["Small"]),
            Paragraph(f"<b>Technical Lead review confidence:</b> {_confidence(agentic.get('review_confidence'))}", styles["Small"]),
        ]
    )
    evidence_items = diagnosis.get("evidence") or []
    if evidence_items:
        story.append(Paragraph("Agent conclusions", styles["Heading3"]))
        for item in evidence_items:
            story.append(Paragraph(f"- {_text(item)}", styles["Small"]))

    remediation = incident.get("remediation") or {}
    story.extend(
        [
            Paragraph("4. Recommended remediation", styles["Section"]),
            Paragraph(
                _text(remediation.get("summary"), "No remediation recommendation has been produced yet."),
                styles["BodyText"],
            ),
        ]
    )
    for index, step in enumerate(remediation.get("steps") or [], start=1):
        if not isinstance(step, dict):
            story.append(Paragraph(f"<b>{index}.</b> {_text(step)}", styles["BodyText"]))
            continue
        title = step.get("title") or step.get("action") or "Operator action"
        target = step.get("target") or incident.get("entity") or incident.get("service") or "-"
        purpose = step.get("purpose") or step.get("description")
        block: list[Any] = [
            Paragraph(f"<b>{index}. {_text(title)}</b>", styles["BodyText"]),
            Paragraph(f"Target: <b>{_text(target)}</b>", styles["Small"]),
        ]
        if purpose:
            block.append(Paragraph(f"Purpose: {_text(purpose)}", styles["Small"]))
        if step.get("command"):
            block.append(Paragraph(_text(step.get("command")), styles["Command"]))
        if step.get("expected_result"):
            block.append(Paragraph(f"Expected result: {_text(step.get('expected_result'))}", styles["Small"]))
        if step.get("what_to_verify"):
            block.append(Paragraph(f"What to verify: {_text(step.get('what_to_verify'))}", styles["Small"]))
        block.append(Spacer(1, 2 * mm))
        story.append(KeepTogether(block))

    verification = remediation.get("verification") or []
    if verification:
        story.append(Paragraph("Verification guidance", styles["Heading3"]))
        for item in verification:
            story.append(Paragraph(f"- {_text(item)}", styles["Small"]))
    risks = remediation.get("risks") or []
    if risks:
        story.append(Paragraph("Operational considerations", styles["Heading3"]))
        for item in risks:
            story.append(Paragraph(f"- {_text(item)}", styles["Small"]))

    validation = incident.get("validation") or {}
    story.extend(
        [
            Paragraph("5. Validation and final outcome", styles["Section"]),
            Paragraph(f"<b>Validation status:</b> {_text(validation.get('status'), 'PENDING')}", styles["BodyText"]),
            Paragraph(f"<b>Validation summary:</b> {_text(validation.get('summary'), 'Validation has not been completed yet.')}", styles["BodyText"]),
            Paragraph(f"<b>Incident status:</b> {_text(incident.get('status'))}", styles["BodyText"]),
        ]
    )

    tasks = incident.get("tasks") or []
    if tasks:
        story.append(Paragraph("6. Durable agent tasks", styles["Section"]))
        for task in tasks:
            story.append(
                Paragraph(
                    f"<b>{_text(task.get('task_type') or 'agent task')}</b> - {_text(task.get('state'))} - "
                    f"assigned to {_text(task.get('assigned_to'))} - attempt {_text(task.get('attempt'))}/{_text(task.get('max_attempts'))}",
                    styles["Small"],
                )
            )
            if task.get("last_error"):
                error = task.get("last_error")
                if isinstance(error, dict):
                    story.append(Paragraph(f"Last error: {_text(error.get('message') or error.get('type'))}", styles["Muted"]))
                else:
                    story.append(Paragraph(f"Last error: {_text(error)}", styles["Muted"]))

    events = incident.get("timeline") or []
    story.append(Paragraph("7. Agent activity timeline", styles["Section"]))
    if not events:
        story.append(Paragraph("No structured agent activity has been recorded yet.", styles["BodyText"]))
    else:
        for index, event in enumerate(events, start=1):
            payload = _event_payload(event)
            action = event.get("action") or event.get("event_type") or "Agent activity"
            timestamp = event.get("timestamp") or event.get("created_at") or "-"
            agent = event.get("agent_role") or event.get("agent_jid") or "system"
            tool = event.get("tool")

            header = f"{index}. {_text(action)}"
            if tool:
                header += f" - tool: {_text(tool)}"
            story.append(Paragraph(header, styles["EventAction"]))
            story.append(Paragraph(f"{_text(timestamp)} - {_text(agent)}", styles["Muted"]))
            story.append(Paragraph(_text(_event_summary(payload)), styles["Small"]))

            for label, value in _event_facts(payload):
                story.append(Paragraph(f"<b>{_text(label)}:</b> {_text(value)}", styles["Small"]))

            if isinstance(payload, (dict, list, tuple)):
                story.append(
                    Paragraph(
                        "Complete structured payload omitted from the PDF for readability; inspect the incident timeline in the dashboard for the full JSON.",
                        styles["Muted"],
                    )
                )
            story.append(Spacer(1, 2.5 * mm))

    story.extend(
        [
            Spacer(1, 4 * mm),
            Paragraph(
                "Raw metrics, raw logs and complete agent JSON payloads remain available through the monitoring stack and dashboard. This PDF is an operator-oriented summary.",
                styles["Muted"],
            ),
        ]
    )

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf = buffer.getvalue()
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("Dashboard incident report generator did not produce a valid PDF document")
    return pdf
