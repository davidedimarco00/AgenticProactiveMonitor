(() => {
  const clock = document.getElementById("live-clock");
  let selectedAgentJid = null;

  const updateClock = () => {
    if (!clock) return;
    const now = new Date();
    clock.textContent = `${now.toISOString().slice(11, 19)} UTC`;
  };

  const escapeHtml = (value) =>
    String(value ?? "")
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
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  };

  const bindRows = () => {
    document.querySelectorAll(".click-row[data-href]").forEach((row) => {
      row.addEventListener("click", () => {
        window.location.href = row.dataset.href;
      });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          window.location.href = row.dataset.href;
        }
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

      if (stateNode) {
        stateNode.textContent = team.state;
        stateNode.classList.toggle("working", Boolean(team.working));
        stateNode.classList.toggle("idle", !team.working);
      }

      if (network) {
        network.classList.toggle("working", Boolean(team.working));
        network.classList.toggle("idle", !team.working);
      }

      const incidentNode = document.getElementById("team-active-incidents");
      if (incidentNode) {
        const suffix = team.active_incidents === 1 ? "incident" : "incidents";
        incidentNode.textContent = `${team.active_incidents} ${suffix} under investigation`;
      }

      (team.members || []).forEach((member) => {
        const node = document.querySelector(`[data-agent-jid="${member.jid}"]`);
        if (!node) return;

        const working = member.activity === "WORKING";
        node.classList.toggle("working", working);
        node.classList.toggle("idle", !working);

        const activity = node.querySelector("[data-agent-activity]");
        if (activity) {
          activity.textContent = member.activity;
          activity.classList.toggle("working", working);
          activity.classList.toggle("idle", !working);
        }
      });
    } catch (_) {
      // Keep the server-rendered state if the lightweight refresh fails.
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
    } catch (_) {
      // Keep the server-rendered state if the live check fails.
    }
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

  const setModalState = (activity) => {
    if (!modalState) return;
    const working = activity === "WORKING";
    modalState.textContent = working ? "WORKING" : "IDLE";
    modalState.classList.toggle("working", working);
    modalState.classList.toggle("idle", !working);
  };

  const renderAgentEvents = (events) => {
    if (!logBody || !logEmpty) return;
    logBody.innerHTML = "";
    const rows = Array.isArray(events) ? events : [];
    logEmpty.hidden = rows.length > 0;

    rows.forEach((event) => {
      const action = escapeHtml(event.action || event.event_type || "Agent activity");
      const calledBy = escapeHtml(event.called_by || "system");
      const reason = escapeHtml(event.reason || "—");
      const tool = escapeHtml(event.tool || "");
      const outcome = escapeHtml(event.outcome || event.status || "—");
      const incident = escapeHtml(event.incident_id || "—");
      const toolOutcome = tool ? `<strong>${tool}</strong><small>${outcome}</small>` : outcome;
      const incidentCell = event.incident_id
        ? `<a href="/incidents/${encodeURIComponent(event.incident_id)}">${incident}</a>`
        : "—";

      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="nowrap">${escapeHtml(formatTimestamp(event.timestamp))}</td>
        <td><strong>${action}</strong>${event.status ? `<small>${escapeHtml(event.status)}</small>` : ""}</td>
        <td>${calledBy}</td>
        <td class="agent-log-reason">${reason}</td>
        <td>${toolOutcome}</td>
        <td>${incidentCell}</td>
      `;
      logBody.appendChild(row);
    });
  };

  const loadAgentActivity = async (jid) => {
    if (!jid || !modal) return;
    selectedAgentJid = jid;
    if (logLoading) logLoading.hidden = false;
    if (logEmpty) logEmpty.hidden = true;

    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(jid)}/activity?limit=100`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const agent = payload.agent || {};
      const events = payload.events || [];

      if (modalName) modalName.textContent = agent.name || jid;
      if (modalJid) modalJid.textContent = agent.jid || jid;
      if (modalRole) modalRole.textContent = agent.role || "Specialised agent";
      setModalState(agent.activity || "IDLE");
      if (eventCount) eventCount.textContent = String(events.length);
      if (lastActivity) lastActivity.textContent = events.length ? formatTimestamp(events[0].timestamp) : "—";
      if (currentIncident) currentIncident.textContent = events.find((event) => event.incident_id)?.incident_id || "—";
      renderAgentEvents(events);
    } catch (_) {
      if (logBody) logBody.innerHTML = "";
      if (logEmpty) {
        logEmpty.hidden = false;
        logEmpty.querySelector("strong").textContent = "Agent activity could not be loaded.";
        logEmpty.querySelector("p").textContent = "Check the dashboard API and OpenSearch connectivity.";
      }
    } finally {
      if (logLoading) logLoading.hidden = true;
    }
  };

  const openAgentModal = (node) => {
    if (!modal || !node) return;
    const jid = node.dataset.agentJid;
    if (!jid) return;

    if (modalName) modalName.textContent = node.dataset.agentName || jid;
    if (modalJid) modalJid.textContent = jid;
    if (modalRole) modalRole.textContent = node.dataset.agentRole || "Specialised agent";
    setModalState(node.classList.contains("working") ? "WORKING" : "IDLE");
    if (eventCount) eventCount.textContent = "—";
    if (lastActivity) lastActivity.textContent = "—";
    if (currentIncident) currentIncident.textContent = "—";
    if (logBody) logBody.innerHTML = "";

    modal.hidden = false;
    document.body.classList.add("modal-open");
    loadAgentActivity(jid);
  };

  const closeAgentModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    selectedAgentJid = null;
  };

  const bindAgentObservability = () => {
    if (!modal) return;

    document.querySelectorAll(".agent-node[data-agent-jid]").forEach((node) => {
      node.addEventListener("click", () => openAgentModal(node));
    });

    document.querySelectorAll("[data-agent-modal-close]").forEach((control) => {
      control.addEventListener("click", closeAgentModal);
    });

    const refreshButton = document.getElementById("agent-modal-refresh");
    refreshButton?.addEventListener("click", () => {
      if (selectedAgentJid) loadAgentActivity(selectedAgentJid);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) closeAgentModal();
    });
  };

  updateClock();
  bindRows();
  bindIncidentSearch();
  bindAgentObservability();
  refreshTeamActivity();
  refreshSystemHealth();

  window.setInterval(updateClock, 1000);
  window.setInterval(refreshTeamActivity, 15000);
  window.setInterval(refreshSystemHealth, 15000);
})();
