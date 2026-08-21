(() => {
  const clock = document.getElementById("live-clock");
  let selectedAgentProfile = null;
  let shuttingDown = false;
  const agentHealthSockets = new Map();
  const agentHealthReconnectTimers = new Map();

  const ROLE_DIRECTORY = {
    "coordinator@xmpp": { name: "Technical Lead", jid: "technical-lead@xmpp", role: "Incident triage, coordination and critical review" },
    "technical-lead@xmpp": { name: "Technical Lead", jid: "technical-lead@xmpp", role: "Incident triage, coordination and critical review" },
    "evidence@xmpp": { name: "System Engineer", jid: "system-engineer@xmpp", role: "Linux, containers, host resources and runtime diagnostics" },
    "system-engineer@xmpp": { name: "System Engineer", jid: "system-engineer@xmpp", role: "Linux, containers, host resources and runtime diagnostics" },
    "critic@xmpp": { name: "Network Engineer", jid: "network-engineer@xmpp", role: "Connectivity, latency, network paths and traffic analysis" },
    "network-engineer@xmpp": { name: "Network Engineer", jid: "network-engineer@xmpp", role: "Connectivity, latency, network paths and traffic analysis" },
    "reasoning@xmpp": { name: "Application Engineer", jid: "application-engineer@xmpp", role: "Service health, application logs and dependency diagnosis" },
    "application-engineer@xmpp": { name: "Application Engineer", jid: "application-engineer@xmpp", role: "Service health, application logs and dependency diagnosis" },
    "remediation@xmpp": { name: "Software Developer", jid: "software-developer@xmpp", role: "Code behaviour, defects and application-level corrective guidance" },
    "software-developer@xmpp": { name: "Software Developer", jid: "software-developer@xmpp", role: "Code behaviour, defects and application-level corrective guidance" },
  };

  const CALLER_DIRECTORY = {
    ...ROLE_DIRECTORY,
    "opensearch-ad": { name: "OpenSearch AD" },
    "opensearch": { name: "OpenSearch" },
    "operator": { name: "Human Operator" },
    "system": { name: "System" },
  };

  const ACTIVITY_STATES = ["IDLE", "WORKING", "WAITING"];
  const ACTIVITY_DETAIL_LABELS = {
    taking_incident_in_charge: "TAKING INCIDENT",
    incident_accepted: "INCIDENT ACCEPTED",
    triaging_incident: "BDI TRIAGE",
    primary_investigator_selected: "INVESTIGATOR SELECTED",
    specialist_bdi_deliberation: "BDI DELIBERATION",
    investigation_intention_committed: "INVESTIGATING",
    collaborative_investigation_intention_committed: "COLLAB BDI",
    react_investigation: "REACT INVESTIGATION",
    peer_collaborative_react_investigation: "COLLAB REACT",
    awaiting_technical_lead_review: "AWAITING TL REVIEW",
    peer_result_shared_awaiting_tl_review: "PEER RESULT SHARED",
    peer_context_received: "PEER CONTEXT RECEIVED",
    peer_result_received: "PEER RESULT RECEIVED",
    reviewing_specialist_result: "REVIEWING RESULT",
    review_decision_committed: "REVIEW DECIDED",
    support_coordination_pending: "SUPPORT PENDING",
    peer_collaboration_in_progress: "COLLABORATING",
    review_failed: "REVIEW FAILED",
    react_investigation_failed: "REACT FAILED",
    task_acceptance_failed: "TASK REJECTED",
  };

  const updateClock = () => {
    if (!clock) return;
    const now = new Date();
    clock.textContent = `${now.toISOString().slice(11, 19)} UTC`;
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const formatTimestamp = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "2-digit", hour: "2-digit",
      minute: "2-digit", second: "2-digit", hour12: false,
    });
  };

  const humanizeIdentity = (value) => {
    if (!value) return "System";
    const normalized = String(value).trim().toLowerCase();
    const profile = CALLER_DIRECTORY[normalized];
    if (profile?.name) return profile.name;
    return String(value);
  };

  const normalizeActivity = (activity) => {
    const normalized = String(activity || "IDLE").trim().toUpperCase();
    return ACTIVITY_STATES.includes(normalized) ? normalized : "IDLE";
  };

  const activityLabel = (activity, detail) => {
    const normalized = normalizeActivity(activity);
    const key = String(detail || "").trim().toLowerCase();
    if (key.startsWith("collaborating_with_")) return "COLLABORATING";
    return ACTIVITY_DETAIL_LABELS[key] || normalized;
  };

  const setAgentActivity = (node, activity, detail = null) => {
    if (!node) return;
    const normalized = normalizeActivity(activity);
    node.dataset.agentActivityState = normalized;
    if (detail !== null && detail !== undefined) {
      node.dataset.agentActivityDetail = String(detail || "");
    }

    ACTIVITY_STATES.forEach((state) => {
      node.classList.toggle(state.toLowerCase(), normalized === state);
    });

    const pill = node.querySelector("[data-agent-activity]");
    if (pill) {
      pill.textContent = activityLabel(normalized, detail ?? node.dataset.agentActivityDetail);
      ACTIVITY_STATES.forEach((state) => {
        pill.classList.toggle(state.toLowerCase(), normalized === state);
      });
    }
  };

  const bindRows = () => {
    document.querySelectorAll(".click-row[data-href]").forEach((row) => {
      row.addEventListener("click", () => { window.location.href = row.dataset.href; });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") window.location.href = row.dataset.href;
      });
      row.tabIndex = 0;
    });
  };

  const bindIncidentSearch = () => {
    const search = document.getElementById("incident-search");
    const table = document.getElementById("incident-table");
    if (!search || !table) return;
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      table.querySelectorAll("tbody tr").forEach((row) => {
        row.hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
      });
    });
  };

  const teamWorkflowLabel = (payload, fallback) => {
    const workflow = payload?.workflow || {};
    const incident = workflow.active_incident || {};
    const status = String(incident.status || "").trim().toUpperCase();
    const agentic = incident.agentic || {};
    if (status === "UNDER_ANALYSIS") {
      if (String(agentic.peer_collaboration_state || "").toUpperCase() === "ACTIVE") return "COLLABORATING";
      if (agentic.support_requested) return "SUPPORT PENDING";
      if (agentic.review_state === "PENDING") return "TL REVIEW";
      return "UNDER ANALYSIS";
    }
    if (status === "TRIAGED") return "INVESTIGATING";
    if (status === "TAKEN_IN_CHARGE") return "TRIAGE";
    if (status === "OPERATOR_ACTION_REQUIRED") return "OPERATOR ACTION";
    if (status === "RESOLVED" || status === "CLOSED") return status;
    return fallback;
  };

  const refreshTeamActivity = async () => {
    const stateNode = document.getElementById("team-activity-state");
    const network = document.getElementById("agent-network");
    if (!stateNode && !network) return;

    try {
      const response = await fetch("/api/overview", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const team = payload.team;
      if (!team) return;

      const teamState = normalizeActivity(team.state);
      const workflowLabel = teamWorkflowLabel(payload, teamState);
      if (stateNode) {
        stateNode.textContent = workflowLabel;
        ACTIVITY_STATES.forEach((state) => {
          stateNode.classList.toggle(state.toLowerCase(), teamState === state);
        });
      }

      if (network) {
        ACTIVITY_STATES.forEach((state) => {
          network.classList.toggle(state.toLowerCase(), teamState === state);
        });
      }

      const incidentNode = document.getElementById("team-active-incidents");
      if (incidentNode) {
        const suffix = team.active_incidents === 1 ? "incident" : "incidents";
        incidentNode.textContent = `${team.active_incidents} ${suffix} with active agent context`;
      }

      (team.members || []).forEach((member) => {
        const node = document.querySelector(`[data-agent-runtime-jid="${member.jid}"]`);
        setAgentActivity(node, member.activity, member.activity_detail);
      });
    } catch (_) {
      // Keep the server-rendered activity state if the lightweight refresh fails.
    }
  };

  const refreshSystemHealth = async () => {
    const strip = document.getElementById("service-strip");
    const overall = document.getElementById("system-overall-state");
    if (!strip || !overall) return;
    try {
      const response = await fetch("/api/system-health", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      overall.textContent = payload.overall_online ? "OPERATIONAL" : "DEGRADED";
      (payload.services || []).forEach((service) => {
        const item = strip.querySelector(`[data-service-name="${service.name}"]`);
        const dot = item?.querySelector(".status-dot");
        if (!dot) return;
        dot.classList.toggle("online", service.status === "ONLINE");
        dot.classList.toggle("offline", service.status !== "ONLINE");
      });
    } catch (_) {}
  };

  const setAgentPresence = (node, status, payload = null) => {
    if (!node) return;
    const normalized = ["ONLINE", "DEGRADED", "OFFLINE"].includes(status) ? status : "UNKNOWN";
    node.dataset.agentPresence = normalized;
    node.classList.toggle("presence-online", normalized === "ONLINE");
    node.classList.toggle("presence-degraded", normalized === "DEGRADED");
    node.classList.toggle("presence-offline", normalized === "OFFLINE");
    node.classList.toggle("presence-unknown", normalized === "UNKNOWN");

    if (payload?.activity) setAgentActivity(node, payload.activity, payload.activity_detail);

    const dot = node.querySelector(".agent-presence-dot");
    if (!dot) return;
    if (payload) {
      const xmpp = payload.xmpp_connected ? "XMPP connected" : "XMPP disconnected";
      const communication = payload.communication_ok ? "communication verified" : "communication not verified";
      dot.title = `Agent health: ${normalized} · ${xmpp} · ${communication}`;
    } else {
      dot.title = `Agent health: ${normalized.toLowerCase()}`;
    }
  };

  const scheduleAgentHealthReconnect = (node) => {
    if (shuttingDown || !node) return;
    const key = node.dataset.agentDisplayJid || node.dataset.agentHealthPort;
    if (!key || agentHealthReconnectTimers.has(key)) return;
    const timer = window.setTimeout(() => {
      agentHealthReconnectTimers.delete(key);
      connectAgentHealth(node);
    }, 2000);
    agentHealthReconnectTimers.set(key, timer);
  };

  const connectAgentHealth = (node) => {
    if (!node || shuttingDown) return;
    const port = Number(node.dataset.agentHealthPort || 0);
    if (!Number.isInteger(port) || port <= 0) {
      setAgentPresence(node, "OFFLINE");
      return;
    }
    const key = node.dataset.agentDisplayJid || String(port);
    const existing = agentHealthSockets.get(key);
    if (existing && existing.readyState <= WebSocket.OPEN) return;

    setAgentPresence(node, "UNKNOWN");
    const host = window.location.hostname || "127.0.0.1";
    const socket = new WebSocket(`ws://${host}:${port}/ws/health`);
    agentHealthSockets.set(key, socket);
    socket.addEventListener("open", () => setAgentPresence(node, "DEGRADED"));
    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);
        setAgentPresence(node, payload.status, payload);
      } catch (_) { setAgentPresence(node, "DEGRADED"); }
    });
    socket.addEventListener("error", () => setAgentPresence(node, "OFFLINE"));
    socket.addEventListener("close", () => {
      if (agentHealthSockets.get(key) === socket) agentHealthSockets.delete(key);
      setAgentPresence(node, "OFFLINE");
      scheduleAgentHealthReconnect(node);
    });
  };

  const bindAgentHealthStreams = () => {
    document.querySelectorAll(".agent-node[data-agent-health-port]").forEach((node) => connectAgentHealth(node));
  };

  const modal = document.getElementById("agent-observability-modal");
  const modalName = document.getElementById("agent-modal-name");
  const modalJid = document.getElementById("agent-modal-jid");
  const modalRole = document.getElementById("agent-modal-role");
  const modalState = document.getElementById("agent-modal-state");
  const eventCount = document.getElementById("agent-event-count");
  const lastActivity = document.getElementById("agent-last-activity");
  const currentIncident = document.getElementById("agent-current-incident");
  const logBody = document.getElementById("agent-log-body");
  const logEmpty = document.getElementById("agent-log-empty");
  const logLoading = document.getElementById("agent-log-loading");

  const setModalState = (activity, detail = null) => {
    if (!modalState) return;
    const normalized = normalizeActivity(activity);
    modalState.textContent = activityLabel(normalized, detail);
    ACTIVITY_STATES.forEach((state) => {
      modalState.classList.toggle(state.toLowerCase(), normalized === state);
    });
  };

  const renderAgentEvents = (events) => {
    if (!logBody || !logEmpty) return;
    logBody.innerHTML = "";
    const rows = Array.isArray(events) ? events : [];
    logEmpty.hidden = rows.length > 0;
    rows.forEach((event) => {
      const action = escapeHtml(event.action || event.event_type || "Agent activity");
      const calledBy = escapeHtml(humanizeIdentity(event.called_by || "system"));
      const reason = escapeHtml(event.reason || "—");
      const tool = escapeHtml(event.tool || "");
      const outcome = escapeHtml(event.outcome || event.status || "—");
      const incident = escapeHtml(event.incident_id || "—");
      const toolOutcome = tool ? `<strong>${tool}</strong><small>${outcome}</small>` : outcome;
      const incidentCell = event.incident_id ? `<a href="/incidents/${encodeURIComponent(event.incident_id)}">${incident}</a>` : "—";
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="nowrap">${escapeHtml(formatTimestamp(event.timestamp))}</td>
        <td><strong>${action}</strong>${event.status ? `<small>${escapeHtml(event.status)}</small>` : ""}</td>
        <td><strong>${calledBy}</strong></td>
        <td class="agent-log-reason">${reason}</td>
        <td>${toolOutcome}</td>
        <td>${incidentCell}</td>
      `;
      logBody.appendChild(row);
    });
  };

  const loadAgentActivity = async (profile) => {
    if (!profile?.runtimeJid || !modal) return;
    selectedAgentProfile = profile;
    if (logLoading) logLoading.hidden = false;
    if (logEmpty) logEmpty.hidden = true;
    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(profile.runtimeJid)}/activity?limit=100`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const agent = payload.agent || {};
      const events = payload.events || [];
      if (modalName) modalName.textContent = profile.name;
      if (modalJid) modalJid.textContent = profile.displayJid;
      if (modalRole) modalRole.textContent = profile.role;
      setModalState(agent.activity || profile.node?.dataset.agentActivityState || "IDLE", agent.activity_detail);
      if (eventCount) eventCount.textContent = String(events.length);
      if (lastActivity) lastActivity.textContent = events.length ? formatTimestamp(events[0].timestamp) : "—";
      if (currentIncident) {
        currentIncident.textContent = agent.activity_incident_id || events.find((event) => event.incident_id)?.incident_id || "—";
      }
      renderAgentEvents(events);
    } catch (_) {
      if (logBody) logBody.innerHTML = "";
      if (logEmpty) {
        logEmpty.hidden = false;
        logEmpty.querySelector("strong").textContent = "Agent activity could not be loaded.";
        logEmpty.querySelector("p").textContent = "Check the dashboard API and backend connectivity.";
      }
    } finally {
      if (logLoading) logLoading.hidden = true;
    }
  };

  const openAgentModal = (node) => {
    if (!modal || !node) return;
    const runtimeJid = node.dataset.agentRuntimeJid;
    if (!runtimeJid) return;
    const fallback = ROLE_DIRECTORY[runtimeJid.toLowerCase()] || {};
    const profile = {
      runtimeJid,
      displayJid: node.dataset.agentDisplayJid || fallback.jid || runtimeJid,
      name: node.dataset.agentName || fallback.name || runtimeJid,
      role: node.dataset.agentRole || fallback.role || "Specialised technical role",
      node,
    };
    if (modalName) modalName.textContent = profile.name;
    if (modalJid) modalJid.textContent = profile.displayJid;
    if (modalRole) modalRole.textContent = profile.role;
    setModalState(node.dataset.agentActivityState || "IDLE", node.dataset.agentActivityDetail);
    if (eventCount) eventCount.textContent = "—";
    if (lastActivity) lastActivity.textContent = "—";
    if (currentIncident) currentIncident.textContent = "—";
    if (logBody) logBody.innerHTML = "";
    modal.hidden = false;
    document.body.classList.add("modal-open");
    loadAgentActivity(profile);
  };

  const closeAgentModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    selectedAgentProfile = null;
  };

  const bindAgentObservability = () => {
    if (!modal) return;
    document.querySelectorAll(".agent-node[data-agent-runtime-jid]").forEach((node) => {
      node.addEventListener("click", () => openAgentModal(node));
    });
    document.querySelectorAll("[data-agent-modal-close]").forEach((control) => control.addEventListener("click", closeAgentModal));
    const refreshButton = document.getElementById("agent-modal-refresh");
    refreshButton?.addEventListener("click", () => { if (selectedAgentProfile) loadAgentActivity(selectedAgentProfile); });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) closeAgentModal();
    });
  };

  window.addEventListener("beforeunload", () => {
    shuttingDown = true;
    agentHealthReconnectTimers.forEach((timer) => window.clearTimeout(timer));
    agentHealthReconnectTimers.clear();
    agentHealthSockets.forEach((socket) => socket.close());
    agentHealthSockets.clear();
  });

  updateClock();
  bindRows();
  bindIncidentSearch();
  bindAgentObservability();
  bindAgentHealthStreams();
  refreshTeamActivity();
  refreshSystemHealth();

  window.setInterval(updateClock, 1000);
  window.setInterval(refreshTeamActivity, 5000);
  window.setInterval(refreshSystemHealth, 15000);
})();
