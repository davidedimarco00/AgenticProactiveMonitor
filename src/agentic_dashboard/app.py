import os
import socket
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request


APP_NAME = "Agentic Monitoring Operator Console"
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
MCP_HOST = os.getenv("MCP_HOST", "mcp-server")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
XMPP_HOST = os.getenv("XMPP_HOST", "xmpp")
XMPP_PORT = int(os.getenv("XMPP_PORT", "5222"))
INCIDENT_INDEX_PREFIX = os.getenv("INCIDENT_INDEX_PREFIX", "agentic-incidents")
AGENT_EVENT_INDEX_PREFIX = os.getenv("AGENT_EVENT_INDEX_PREFIX", "agentic-agent-events")
REQUEST_TIMEOUT = float(os.getenv("DASHBOARD_REQUEST_TIMEOUT_SECONDS", "2.5"))

ACTIVE_STATUSES = {
    "NEW",
    "TAKEN_IN_CHARGE",
    "UNDER_ANALYSIS",
    "DIAGNOSED",
    "OPERATOR_ACTION_REQUIRED",
}

WORKING_STATUSES = {"TAKEN_IN_CHARGE", "UNDER_ANALYSIS"}

AGENT_TEAM = [
    {
        "key": "coordinator",
        "name": "Coordinator",
        "jid": "coordinator@xmpp",
        "role": "Incident orchestration and task coordination",
    },
    {
        "key": "evidence",
        "name": "Evidence",
        "jid": "evidence@xmpp",
        "role": "Telemetry, log and tool evidence collection",
    },
    {
        "key": "reasoning",
        "name": "Reasoning",
        "jid": "reasoning@xmpp",
        "role": "ReAct diagnosis and hypothesis refinement",
    },
    {
        "key": "critic",
        "name": "Critic",
        "jid": "critic@xmpp",
        "role": "Diagnosis validation and contradiction checks",
    },
    {
        "key": "remediation",
        "name": "Remediation",
        "jid": "remediation@xmpp",
        "role": "Operator remediation guidance",
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


def monthly_index(prefix: str, value: str | None) -> str:
    stamp = parse_timestamp(value)
    return f"{prefix}-{stamp:%Y.%m}"


def index_for_timestamp(value: str | None) -> str:
    return monthly_index(INCIDENT_INDEX_PREFIX, value)


def opensearch_request(method: str, path: str, **kwargs: Any) -> requests.Response:
    return http.request(
        method,
        f"{OPENSEARCH_URL}{path}",
        timeout=REQUEST_TIMEOUT,
        **kwargs,
    )


def ensure_incident_template() -> None:
    template = {
        "index_patterns": [f"{INCIDENT_INDEX_PREFIX}-*"],
        "priority": 250,
        "template": {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "dynamic": True,
                "properties": {
                    "incident_id": {"type": "keyword"},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "status": {"type": "keyword"},
                    "severity": {"type": "keyword"},
                    "entity": {"type": "keyword"},
                    "service": {"type": "keyword"},
                    "machine_role": {"type": "keyword"},
                    "takeover_reason": {"type": "text"},
                    "diagnosis": {
                        "properties": {
                            "summary": {"type": "text"},
                            "confidence": {"type": "float"},
                        }
                    },
                    "anomaly": {
                        "properties": {
                            "detector_id": {"type": "keyword"},
                            "metric": {"type": "keyword"},
                            "grade": {"type": "float"},
                            "confidence": {"type": "float"},
                            "observed_value": {"type": "float"},
                            "baseline_value": {"type": "float"},
                        }
                    },
                },
            },
        },
    }
    try:
        response = opensearch_request(
            "PUT",
            "/_index_template/agentic-dashboard-incidents",
            json=template,
        )
        response.raise_for_status()
    except requests.RequestException:
        app.logger.warning("OpenSearch incident template could not be ensured")


def ensure_agent_event_template() -> None:
    template = {
        "index_patterns": [f"{AGENT_EVENT_INDEX_PREFIX}-*"],
        "priority": 255,
        "template": {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "dynamic": True,
                "properties": {
                    "event_id": {"type": "keyword"},
                    "timestamp": {"type": "date"},
                    "agent_jid": {"type": "keyword"},
                    "agent_name": {"type": "keyword"},
                    "action": {"type": "text"},
                    "called_by": {"type": "keyword"},
                    "reason": {"type": "text"},
                    "incident_id": {"type": "keyword"},
                    "tool": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "outcome": {"type": "text"},
                },
            },
        },
    }
    try:
        response = opensearch_request(
            "PUT",
            "/_index_template/agentic-dashboard-agent-events",
            json=template,
        )
        response.raise_for_status()
    except requests.RequestException:
        app.logger.warning("OpenSearch agent-event template could not be ensured")


def normalize_incident(payload: dict[str, Any], incident_id: str | None = None) -> dict[str, Any]:
    now = utc_now()
    normalized = deepcopy(payload)
    normalized["incident_id"] = incident_id or normalized.get("incident_id") or (
        f"INC-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6].upper()}"
    )
    normalized.setdefault("created_at", now)
    normalized["updated_at"] = now
    normalized.setdefault("status", "NEW")
    normalized.setdefault("severity", "MEDIUM")
    normalized.setdefault("entity", normalized.get("service", "unknown"))
    normalized.setdefault("service", normalized.get("entity", "unknown"))
    normalized.setdefault("machine_role", "unknown")
    normalized.setdefault("takeover_reason", "")
    normalized.setdefault("anomaly", {})
    normalized.setdefault("diagnosis", {})
    normalized.setdefault("remediation", {})
    normalized.setdefault("timeline", [])
    normalized.setdefault("agentic", {})
    normalized.setdefault("agent_events", [])

    normalized["status"] = str(normalized["status"]).upper()
    normalized["severity"] = str(normalized["severity"]).upper()
    return normalized


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def agent_definition(identity: str | None) -> dict[str, Any] | None:
    if not identity:
        return None
    normalized = str(identity).strip().lower()
    for definition in AGENT_TEAM:
        if normalized in {definition["key"].lower(), definition["jid"].lower()}:
            return definition
    return None


def normalize_agent_event(
    payload: dict[str, Any], incident_id: str | None = None
) -> dict[str, Any]:
    normalized = deepcopy(payload)
    identity = normalized.get("agent_jid") or normalized.get("agent")
    definition = agent_definition(str(identity) if identity else None)

    if definition is not None:
        jid = definition["jid"]
    elif identity:
        jid = str(identity).strip().lower()
    else:
        raise ValueError("agent_jid is required")

    timestamp = normalized.get("timestamp") or utc_now()
    normalized["event_id"] = normalized.get("event_id") or (
        f"AEV-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8].upper()}"
    )
    normalized["timestamp"] = timestamp
    normalized["agent_jid"] = jid
    normalized["agent_name"] = normalized.get("agent_name") or (
        definition["name"] if definition else jid.split("@", 1)[0].replace("-", " ").title()
    )
    normalized["action"] = normalized.get("action") or normalized.get("event_type") or "Agent activity"
    normalized["called_by"] = normalized.get("called_by") or "system"
    normalized["reason"] = normalized.get("reason") or ""
    normalized["incident_id"] = normalized.get("incident_id") or incident_id
    normalized["tool"] = normalized.get("tool") or ""
    normalized["status"] = str(normalized.get("status") or "COMPLETED").upper()
    normalized["outcome"] = normalized.get("outcome") or ""
    return normalized


def search_incidents(limit: int = 100) -> list[dict[str, Any]]:
    body = {
        "size": min(max(limit, 1), 500),
        "sort": [{"updated_at": {"order": "desc", "unmapped_type": "date"}}],
        "query": {"match_all": {}},
    }
    try:
        response = opensearch_request(
            "POST",
            f"/{INCIDENT_INDEX_PREFIX}-*/_search?ignore_unavailable=true&allow_no_indices=true",
            json=body,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        incidents = []
        for hit in hits:
            source = hit.get("_source", {})
            source["_index"] = hit.get("_index")
            incidents.append(source)
        return incidents
    except requests.RequestException:
        app.logger.warning("Could not load incidents from OpenSearch")
        return []


def search_agent_events(
    agent_jid: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"match_all": {}}
    if agent_jid:
        query = {"term": {"agent_jid": agent_jid.lower()}}

    body = {
        "size": min(max(limit, 1), 500),
        "sort": [{"timestamp": {"order": "desc", "unmapped_type": "date"}}],
        "query": query,
    }
    try:
        response = opensearch_request(
            "POST",
            f"/{AGENT_EVENT_INDEX_PREFIX}-*/_search?ignore_unavailable=true&allow_no_indices=true",
            json=body,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        events = []
        for hit in hits:
            source = hit.get("_source", {})
            source["_index"] = hit.get("_index")
            events.append(source)
        return events
    except requests.RequestException:
        app.logger.warning("Could not load agent events from OpenSearch")
        return []


def get_incident(incident_id: str) -> dict[str, Any] | None:
    body = {
        "size": 1,
        "query": {"term": {"incident_id": incident_id}},
        "sort": [{"updated_at": {"order": "desc", "unmapped_type": "date"}}],
    }
    try:
        response = opensearch_request(
            "POST",
            f"/{INCIDENT_INDEX_PREFIX}-*/_search?ignore_unavailable=true&allow_no_indices=true",
            json=body,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        if not hits:
            return None
        source = hits[0].get("_source", {})
        source["_index"] = hits[0].get("_index")
        return source
    except requests.RequestException:
        return None


def save_incident(incident: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in incident.items() if not k.startswith("_")}
    index = incident.get("_index") or index_for_timestamp(clean.get("created_at"))
    response = opensearch_request(
        "PUT",
        f"/{index}/_doc/{clean['incident_id']}?refresh=true",
        json=clean,
    )
    response.raise_for_status()
    clean["_index"] = index
    return clean


def save_agent_event(event: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in event.items() if not k.startswith("_")}
    index = event.get("_index") or monthly_index(
        AGENT_EVENT_INDEX_PREFIX, clean.get("timestamp")
    )
    response = opensearch_request(
        "PUT",
        f"/{index}/_doc/{clean['event_id']}?refresh=true",
        json=clean,
    )
    response.raise_for_status()
    clean["_index"] = index
    return clean


def persist_embedded_agent_events(incident: dict[str, Any]) -> int:
    saved_count = 0
    for payload in incident.get("agent_events", []) or []:
        if not isinstance(payload, dict):
            continue
        try:
            event = normalize_agent_event(payload, incident.get("incident_id"))
            save_agent_event(event)
            saved_count += 1
        except (ValueError, requests.RequestException):
            app.logger.warning(
                "Embedded agent event could not be persisted for incident %s",
                incident.get("incident_id"),
            )
    return saved_count


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
    except requests.RequestException as exc:
        return False, str(exc), None


def system_health() -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []

    os_ok, os_detail, os_data = http_check(f"{OPENSEARCH_URL}/_cluster/health")
    cluster_status = (os_data or {}).get("status")
    services.append(
        {
            "name": "OpenSearch",
            "role": "Anomalies, metrics, logs and incident history",
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
            "role": "Local LLM reasoning",
            "status": "ONLINE" if ollama_ok else "OFFLINE",
            "detail": f"{models} model(s) available" if ollama_ok else ollama_detail,
            "critical": True,
        }
    )

    return services


def build_overview(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    active = [i for i in incidents if i.get("status", "").upper() in ACTIVE_STATUSES]
    under_analysis = [
        i for i in incidents if i.get("status", "").upper() == "UNDER_ANALYSIS"
    ]
    operator_action = [
        i
        for i in incidents
        if i.get("status", "").upper() == "OPERATOR_ACTION_REQUIRED"
    ]
    resolved = [
        i for i in incidents if i.get("status", "").upper() in {"RESOLVED", "CLOSED"}
    ]
    critical = [i for i in active if i.get("severity", "").upper() == "CRITICAL"]
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
        active_agents.add("coordinator@xmpp")

    members = []
    for definition in AGENT_TEAM:
        aliases = {definition["key"].lower(), definition["jid"].lower()}
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
        overall_online=all(
            s["status"] == "ONLINE" for s in health if s["critical"]
        ),
        now=utc_now(),
    )


@app.route("/incidents")
def incidents_page() -> str:
    incidents = search_incidents(300)
    status = request.args.get("status", "").strip().upper()
    query = request.args.get("q", "").strip().lower()

    if status:
        incidents = [i for i in incidents if i.get("status", "").upper() == status]
    if query:
        incidents = [
            i
            for i in incidents
            if query
            in " ".join(
                [
                    str(i.get("incident_id", "")),
                    str(i.get("entity", "")),
                    str(i.get("service", "")),
                    str(i.get("takeover_reason", "")),
                    str(i.get("diagnosis", {}).get("summary", "")),
                    str(i.get("remediation", {}).get("summary", "")),
                ]
            ).lower()
        ]

    return render_template(
        "incidents.html",
        app_name=APP_NAME,
        incidents=incidents,
        selected_status=status,
        query=request.args.get("q", ""),
    )


@app.route("/incidents/<incident_id>")
def incident_detail(incident_id: str):
    incident = get_incident(incident_id)
    if incident is None:
        return render_template(
            "not_found.html", app_name=APP_NAME, incident_id=incident_id
        ), 404
    return render_template(
        "incident_detail.html", app_name=APP_NAME, incident=incident
    )


@app.route("/system")
def system_page() -> str:
    health = system_health()
    incidents = search_incidents(300)
    return render_template(
        "system.html",
        app_name=APP_NAME,
        services=health,
        team=build_agent_team(incidents),
        overall_online=all(
            s["status"] == "ONLINE" for s in health if s["critical"]
        ),
        now=utc_now(),
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
            "overall_online": all(
                s["status"] == "ONLINE" for s in services if s["critical"]
            ),
            "services": services,
        }
    )


@app.route("/api/incidents", methods=["GET", "POST"])
def api_incidents():
    if request.method == "GET":
        return jsonify(
            {"incidents": search_incidents(int(request.args.get("limit", "100")))}
        )

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    incident = normalize_incident(payload)
    try:
        saved = save_incident(incident)
        events_saved = persist_embedded_agent_events(saved)
        response_payload = deepcopy(saved)
        response_payload["_observability_events_saved"] = events_saved
        return jsonify(response_payload), 201
    except requests.RequestException as exc:
        app.logger.exception("Incident could not be persisted")
        return jsonify(
            {"error": "OpenSearch persistence failed", "detail": str(exc)}
        ), 503


@app.route("/api/incidents/<incident_id>", methods=["GET", "PATCH"])
def api_incident(incident_id: str):
    incident = get_incident(incident_id)
    if incident is None:
        return jsonify({"error": "Incident not found"}), 404

    if request.method == "GET":
        return jsonify(incident)

    patch = request.get_json(silent=True)
    if not isinstance(patch, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    merged = deep_merge(incident, patch)
    merged["incident_id"] = incident_id
    merged["created_at"] = incident.get("created_at", merged.get("created_at"))
    merged["updated_at"] = utc_now()
    merged["status"] = str(merged.get("status", "NEW")).upper()
    merged["severity"] = str(merged.get("severity", "MEDIUM")).upper()

    try:
        saved = save_incident(merged)
        if "agent_events" in patch:
            persist_embedded_agent_events(saved)
        return jsonify(saved)
    except requests.RequestException as exc:
        app.logger.exception("Incident could not be updated")
        return jsonify(
            {"error": "OpenSearch persistence failed", "detail": str(exc)}
        ), 503


@app.route("/api/agent-events", methods=["GET", "POST"])
def api_agent_events():
    if request.method == "GET":
        agent_jid = request.args.get("agent_jid", "").strip().lower() or None
        limit = int(request.args.get("limit", "100"))
        return jsonify({"events": search_agent_events(agent_jid, limit)})

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    try:
        event = normalize_agent_event(payload)
        saved = save_agent_event(event)
        return jsonify(saved), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.RequestException as exc:
        app.logger.exception("Agent event could not be persisted")
        return jsonify(
            {"error": "OpenSearch persistence failed", "detail": str(exc)}
        ), 503


@app.get("/api/agents/<path:agent_jid>/activity")
def api_agent_activity(agent_jid: str):
    identity = agent_jid.strip().lower()
    definition = agent_definition(identity)
    canonical_jid = definition["jid"] if definition else identity

    incidents = search_incidents(300)
    team = build_agent_team(incidents)
    member = next(
        (
            item
            for item in team["members"]
            if item["jid"].lower() == canonical_jid.lower()
        ),
        None,
    )

    if member is None:
        member = {
            "key": canonical_jid.split("@", 1)[0],
            "name": canonical_jid.split("@", 1)[0].replace("-", " ").title(),
            "jid": canonical_jid,
            "role": "Specialised agent",
            "activity": "IDLE",
        }

    limit = int(request.args.get("limit", "100"))
    events = search_agent_events(canonical_jid, limit)
    return jsonify(
        {
            "generated_at": utc_now(),
            "agent": member,
            "events": events,
        }
    )


@app.get("/health")
def health():
    try:
        response = opensearch_request("GET", "/_cluster/health")
        if response.ok:
            return jsonify({"status": "ok", "opensearch": "reachable"})
    except requests.RequestException:
        pass
    return jsonify({"status": "degraded", "opensearch": "unreachable"}), 503


ensure_incident_template()
ensure_agent_event_template()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
