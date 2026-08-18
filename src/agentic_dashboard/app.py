import os
import socket
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request, send_file


APP_NAME = "Agentic Monitoring Operator Console"
AGENTIC_BACKEND_URL = os.getenv("AGENTIC_BACKEND_URL", "http://agentic-backend:8082").rstrip("/")
AGENTIC_BACKEND_PUBLIC_URL = os.getenv(
    "AGENTIC_BACKEND_PUBLIC_URL", "http://localhost:8082"
).rstrip("/")
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
MCP_HOST = os.getenv("MCP_HOST", "mcp-server")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
XMPP_HOST = os.getenv("XMPP_HOST", "xmpp")
XMPP_PORT = int(os.getenv("XMPP_PORT", "5222"))
REQUEST_TIMEOUT = float(os.getenv("DASHBOARD_REQUEST_TIMEOUT_SECONDS", "2.5"))

ACTIVE_STATUSES = {
    "NEW",
    "TAKEN_IN_CHARGE",
    "UNDER_ANALYSIS",
    "DIAGNOSED",
    "OPERATOR_ACTION_REQUIRED",
}
WORKING_STATUSES = {"TAKEN_IN_CHARGE", "UNDER_ANALYSIS"}

# The keys are retained for compatibility with the existing dashboard layout.
AGENT_TEAM = [
    {
        "key": "coordinator",
        "name": "Technical Lead",
        "jid": "technical-lead@xmpp",
        "backend_role": "technical_lead",
        "role": "Incident triage, coordination and critical review",
    },
    {
        "key": "evidence",
        "name": "System Engineer",
        "jid": "system-engineer@xmpp",
        "backend_role": "system_engineer",
        "role": "Linux, containers, host resources and runtime diagnostics",
    },
    {
        "key": "critic",
        "name": "Network Engineer",
        "jid": "network-engineer@xmpp",
        "backend_role": "network_engineer",
        "role": "Connectivity, latency, network paths and traffic analysis",
    },
    {
        "key": "reasoning",
        "name": "Application Engineer",
        "jid": "application-engineer@xmpp",
        "backend_role": "application_engineer",
        "role": "Service health, application behaviour and dependency diagnosis",
    },
    {
        "key": "remediation",
        "name": "Software Developer",
        "jid": "software-developer@xmpp",
        "backend_role": "software_developer",
        "role": "Code behaviour, defects and application-level corrective guidance",
    },
]

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
http = requests.Session()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return datetime.now(timezone.utc)


def backend_request(method: str, path: str, **kwargs: Any) -> requests.Response:
    return http.request(
        method,
        f"{AGENTIC_BACKEND_URL}{path}",
        timeout=REQUEST_TIMEOUT,
        **kwargs,
    )


def search_incidents(
    limit: int = 100,
    *,
    status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if status:
        params["status"] = status
    if query:
        params["q"] = query
    try:
        response = backend_request("GET", "/api/v1/incidents", params=params)
        response.raise_for_status()
        return response.json().get("incidents", [])
    except (requests.RequestException, ValueError):
        app.logger.warning("Could not load incidents from the agentic backend API")
        return []


def get_incident(incident_id: str) -> dict[str, Any] | None:
    try:
        response = backend_request("GET", f"/api/v1/incidents/{incident_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        app.logger.warning("Could not load incident %s from the backend API", incident_id)
        return None


def agent_definition(identity: str | None) -> dict[str, Any] | None:
    if not identity:
        return None
    normalized = str(identity).strip().lower()
    for definition in AGENT_TEAM:
        aliases = {
            definition["key"].lower(),
            definition["jid"].lower(),
            definition["backend_role"].lower(),
            definition["jid"].split("@", 1)[0].replace("-", "_").lower(),
        }
        if normalized in aliases:
            return definition
    return None


def search_agent_events(agent_identity: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    definition = agent_definition(agent_identity)
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if definition:
        params["agent_role"] = definition["backend_role"]
    elif agent_identity:
        params["agent_role"] = agent_identity
    try:
        response = backend_request("GET", "/api/v1/agent-events", params=params)
        response.raise_for_status()
        return response.json().get("events", [])
    except (requests.RequestException, ValueError):
        return []


def tcp_check(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=REQUEST_TIMEOUT):
            return True, f"TCP {port} reachable"
    except OSError as exc:
        return False, str(exc)


def http_check(
    url: str, expected: tuple[int, ...] = (200,)
) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        response = http.get(url, timeout=REQUEST_TIMEOUT)
        ok = response.status_code in expected
        data = None
        if "application/json" in response.headers.get("content-type", ""):
            data = response.json()
        return ok, f"HTTP {response.status_code}", data
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc), None


def system_health() -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []

    backend_ok, backend_detail, backend_data = http_check(f"{AGENTIC_BACKEND_URL}/health")
    services.append(
        {
            "name": "Agentic Backend API",
            "role": "Incidents, diagnoses, remediation, reports and operator API",
            "status": "ONLINE" if backend_ok else "OFFLINE",
            "detail": backend_detail,
            "critical": True,
        }
    )
    mongo_ok = bool((backend_data or {}).get("mongodb") == "reachable")
    services.append(
        {
            "name": "MongoDB",
            "role": "Agentic incident and history persistence",
            "status": "ONLINE" if mongo_ok else "OFFLINE",
            "detail": "reachable through backend" if mongo_ok else "backend reports unavailable",
            "critical": True,
        }
    )

    os_ok, os_detail, os_data = http_check(f"{OPENSEARCH_URL}/_cluster/health")
    cluster_status = (os_data or {}).get("status")
    services.append(
        {
            "name": "OpenSearch",
            "role": "Metrics, logs and single-entity anomaly detection",
            "status": "ONLINE" if os_ok else "OFFLINE",
            "detail": f"cluster {cluster_status}" if cluster_status else os_detail,
            "critical": True,
        }
    )

    q_ok, q_detail, _ = http_check(f"{QDRANT_URL}/healthz", expected=(200, 204))
    services.append(
        {
            "name": "Qdrant",
            "role": "RAG knowledge base",
            "status": "ONLINE" if q_ok else "OFFLINE",
            "detail": q_detail,
            "critical": True,
        }
    )

    mcp_ok, mcp_detail = tcp_check(MCP_HOST, MCP_PORT)
    services.append(
        {
            "name": "MCP Server",
            "role": "Diagnostic tools",
            "status": "ONLINE" if mcp_ok else "OFFLINE",
            "detail": mcp_detail,
            "critical": True,
        }
    )

    xmpp_ok, xmpp_detail = tcp_check(XMPP_HOST, XMPP_PORT)
    services.append(
        {
            "name": "Prosody XMPP",
            "role": "SPADE agent communication",
            "status": "ONLINE" if xmpp_ok else "OFFLINE",
            "detail": xmpp_detail,
            "critical": True,
        }
    )

    ollama_ok, ollama_detail, ollama_data = http_check(f"{OLLAMA_URL}/api/tags")
    models = len((ollama_data or {}).get("models", [])) if ollama_ok else 0
    services.append(
        {
            "name": "Ollama",
            "role": "Local LLM reasoning and tool selection",
            "status": "ONLINE" if ollama_ok else "OFFLINE",
            "detail": f"{models} model(s) available" if ollama_ok else ollama_detail,
            "critical": True,
        }
    )

    return services


def build_overview(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    active = [i for i in incidents if str(i.get("status", "")).upper() in ACTIVE_STATUSES]
    under_analysis = [i for i in incidents if str(i.get("status", "")).upper() == "UNDER_ANALYSIS"]
    operator_action = [i for i in incidents if str(i.get("status", "")).upper() == "OPERATOR_ACTION_REQUIRED"]
    resolved = [i for i in incidents if str(i.get("status", "")).upper() in {"RESOLVED", "CLOSED"}]
    critical = [i for i in active if str(i.get("severity", "")).upper() == "CRITICAL"]
    return {
        "total": len(incidents),
        "active": len(active),
        "under_analysis": len(under_analysis),
        "operator_action": len(operator_action),
        "resolved": len(resolved),
        "critical": len(critical),
    }


def build_agent_team(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    working_incidents = [
        incident
        for incident in incidents
        if str(incident.get("status", "")).upper() in WORKING_STATUSES
    ]

    active_agents: set[str] = set()
    for incident in working_incidents:
        agentic = incident.get("agentic") or {}
        current_agent = agentic.get("current_agent")
        if current_agent:
            active_agents.add(str(current_agent).lower())
        for active_agent in agentic.get("active_agents", []) or []:
            active_agents.add(str(active_agent).lower())

    if working_incidents and not active_agents:
        active_agents.add("technical_lead")

    members = []
    for definition in AGENT_TEAM:
        aliases = {
            definition["key"].lower(),
            definition["jid"].lower(),
            definition["backend_role"].lower(),
        }
        working = bool(aliases.intersection(active_agents))
        members.append({**definition, "activity": "WORKING" if working else "IDLE"})

    return {
        "state": "WORKING" if working_incidents else "IDLE",
        "working": bool(working_incidents),
        "active_incidents": len(working_incidents),
        "members": members,
    }


@app.template_filter("human_time")
def human_time(value: str | None) -> str:
    if not value:
        return "—"
    parsed = parse_timestamp(value).astimezone(timezone.utc)
    return parsed.strftime("%d %b %Y · %H:%M:%S UTC")


@app.template_filter("confidence")
def confidence(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:.0f}%"


@app.route("/")
def dashboard() -> str:
    incidents = search_incidents(300)
    health = system_health()
    return render_template(
        "dashboard.html",
        app_name=APP_NAME,
        overview=build_overview(incidents),
        incidents=incidents[:100],
        services=health,
        team=build_agent_team(incidents),
        overall_online=all(s["status"] == "ONLINE" for s in health if s["critical"]),
        now=utc_now(),
    )


@app.route("/incidents")
def incidents_page() -> str:
    status = request.args.get("status", "").strip().upper() or None
    query = request.args.get("q", "").strip() or None
    incidents = search_incidents(300, status=status, query=query)
    return render_template(
        "incidents.html",
        app_name=APP_NAME,
        incidents=incidents,
        selected_status=status or "",
        query=request.args.get("q", ""),
    )


@app.route("/incidents/<incident_id>")
def incident_detail(incident_id: str):
    incident = get_incident(incident_id)
    if incident is None:
        return render_template("not_found.html", app_name=APP_NAME, incident_id=incident_id), 404
    return render_template("incident_detail.html", app_name=APP_NAME, incident=incident)


@app.get("/incidents/<incident_id>/report.pdf")
def incident_report(incident_id: str):
    try:
        response = backend_request("GET", f"/api/v1/incidents/{incident_id}/report")
        if response.status_code == 404:
            return render_template("not_found.html", app_name=APP_NAME, incident_id=incident_id), 404
        response.raise_for_status()
        return send_file(
            BytesIO(response.content),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{incident_id}-incident-report.pdf",
        )
    except requests.RequestException:
        return jsonify({"error": "Incident report is temporarily unavailable"}), 503


@app.route("/system")
def system_page() -> str:
    health = system_health()
    incidents = search_incidents(300)
    return render_template(
        "system.html",
        app_name=APP_NAME,
        services=health,
        team=build_agent_team(incidents),
        overall_online=all(s["status"] == "ONLINE" for s in health if s["critical"]),
        now=utc_now(),
        backend_docs_url=f"{AGENTIC_BACKEND_PUBLIC_URL}/docs",
    )


@app.get("/api/overview")
def api_overview():
    incidents = search_incidents(200)
    return jsonify(
        {
            "generated_at": utc_now(),
            "overview": build_overview(incidents),
            "team": build_agent_team(incidents),
            "recent_incidents": incidents[:7],
        }
    )


@app.get("/api/system-health")
def api_system_health():
    services = system_health()
    return jsonify(
        {
            "generated_at": utc_now(),
            "overall_online": all(s["status"] == "ONLINE" for s in services if s["critical"]),
            "services": services,
        }
    )


@app.get("/api/incidents")
def api_incidents():
    status = request.args.get("status", "").strip() or None
    query = request.args.get("q", "").strip() or None
    limit = int(request.args.get("limit", "100"))
    return jsonify({"incidents": search_incidents(limit, status=status, query=query)})


@app.get("/api/incidents/<incident_id>")
def api_incident(incident_id: str):
    incident = get_incident(incident_id)
    if incident is None:
        return jsonify({"error": "Incident not found"}), 404
    return jsonify(incident)


@app.get("/api/agent-events")
def api_agent_events():
    identity = request.args.get("agent_jid", "").strip() or request.args.get("agent_role", "").strip() or None
    limit = int(request.args.get("limit", "100"))
    return jsonify({"events": search_agent_events(identity, limit)})


@app.get("/api/agents/<path:agent_jid>/activity")
def api_agent_activity(agent_jid: str):
    identity = agent_jid.strip().lower()
    definition = agent_definition(identity)
    canonical = definition or {
        "key": identity.split("@", 1)[0],
        "name": identity.split("@", 1)[0].replace("-", " ").title(),
        "jid": identity,
        "backend_role": identity.split("@", 1)[0].replace("-", "_"),
        "role": "Specialised agent",
    }

    incidents = search_incidents(300)
    team = build_agent_team(incidents)
    member = next(
        (item for item in team["members"] if item["jid"].lower() == canonical["jid"].lower()),
        {**canonical, "activity": "IDLE"},
    )
    limit = int(request.args.get("limit", "100"))
    events = search_agent_events(canonical["backend_role"], limit)
    return jsonify({"generated_at": utc_now(), "agent": member, "events": events})


@app.get("/health")
def health():
    ok, detail, data = http_check(f"{AGENTIC_BACKEND_URL}/health")
    if ok:
        return jsonify({"status": "ok", "backend": "reachable", "backend_health": data})
    return jsonify({"status": "degraded", "backend": detail}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
