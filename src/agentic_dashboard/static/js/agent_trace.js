(() => {
  const modal = document.getElementById("agent-observability-modal");
  const body = document.getElementById("agent-log-body");
  const empty = document.getElementById("agent-log-empty");
  const loading = document.getElementById("agent-log-loading");
  const eventCount = document.getElementById("agent-event-count");
  const lastActivity = document.getElementById("agent-last-activity");
  const currentIncident = document.getElementById("agent-current-incident");
  if (!modal || !body) return;

  let selectedJid = null;
  let refreshTimer = null;
  let requestInFlight = false;

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

  const stringify = (value) => {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (_) {
      return String(value);
    }
  };

  const appendText = (row, value, className = "") => {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = String(value ?? "—");
    row.appendChild(cell);
    return cell;
  };

  const appendAction = (row, event) => {
    const cell = document.createElement("td");
    const strong = document.createElement("strong");
    strong.textContent = event.action || event.event_type || "agent_activity";
    cell.appendChild(strong);
    if (event.status) {
      const small = document.createElement("small");
      small.textContent = event.status;
      cell.appendChild(small);
    }
    row.appendChild(cell);
  };

  const appendDisclosure = (cell, label, value) => {
    if (value === undefined || value === null) return;
    const disclosure = document.createElement("details");
    disclosure.className = "agent-trace-details";
    const summary = document.createElement("summary");
    summary.textContent = label;
    const pre = document.createElement("pre");
    pre.textContent = stringify(value);
    disclosure.append(summary, pre);
    cell.appendChild(disclosure);
  };

  const appendTool = (row, event) => {
    const cell = document.createElement("td");
    if (event.tool) {
      const strong = document.createElement("strong");
      strong.textContent = event.tool;
      cell.appendChild(strong);
    }
    const outcome = document.createElement("small");
    outcome.textContent = event.outcome || event.status || "—";
    cell.appendChild(outcome);

    const details = event.details && typeof event.details === "object" ? event.details : null;
    if (details && Object.keys(details).length) {
      const hasObservationViews = Object.prototype.hasOwnProperty.call(details, "raw_observation")
        || Object.prototype.hasOwnProperty.call(details, "reasoning_observation");

      if (hasObservationViews) {
        appendDisclosure(cell, "View reasoning observation", details.reasoning_observation);
        appendDisclosure(
          cell,
          event.action === "rag_retrieval" ? "View complete retrieved knowledge" : "View complete MCP output",
          details.raw_observation,
        );

        const metadata = { ...details };
        delete metadata.reasoning_observation;
        delete metadata.raw_observation;
        if (Object.keys(metadata).length) appendDisclosure(cell, "View trace metadata", metadata);
      } else {
        appendDisclosure(
          cell,
          event.action === "rag_retrieval" ? "View retrieved knowledge" : "View details",
          details,
        );
      }
    }
    row.appendChild(cell);
  };

  const appendIncident = (row, incidentId) => {
    const cell = document.createElement("td");
    if (incidentId) {
      const link = document.createElement("a");
      link.href = `/incidents/${encodeURIComponent(incidentId)}`;
      link.textContent = incidentId;
      cell.appendChild(link);
    } else {
      cell.textContent = "—";
    }
    row.appendChild(cell);
  };

  const render = (payload) => {
    const events = Array.isArray(payload?.events) ? payload.events : [];
    body.replaceChildren();
    if (empty) empty.hidden = events.length > 0;
    if (eventCount) eventCount.textContent = String(events.length);
    if (lastActivity) lastActivity.textContent = events.length ? formatTimestamp(events[0].timestamp) : "—";
    if (currentIncident) {
      currentIncident.textContent = payload?.agent?.activity_incident_id
        || events.find((event) => event.incident_id)?.incident_id
        || "—";
    }

    events.forEach((event) => {
      const row = document.createElement("tr");
      if (event.event_type === "AGENT_EXECUTION_TRACE") row.classList.add("live-agent-trace");
      appendText(row, formatTimestamp(event.timestamp), "nowrap");
      appendAction(row, event);
      appendText(row, event.called_by || event.agent_role || "system");
      appendText(row, event.reason || "—", "agent-log-reason");
      appendTool(row, event);
      appendIncident(row, event.incident_id);
      body.appendChild(row);
    });
  };

  const refresh = async () => {
    if (!selectedJid || modal.hidden || requestInFlight) return;
    requestInFlight = true;
    if (loading) loading.hidden = false;
    try {
      const response = await fetch(
        `/api/agents/${encodeURIComponent(selectedJid)}/activity?limit=150`,
        { cache: "no-store" },
      );
      if (!response.ok) return;
      render(await response.json());
    } catch (_) {
      // Keep the last successful trace visible during transient refresh failures.
    } finally {
      requestInFlight = false;
      if (loading) loading.hidden = true;
    }
  };

  const start = (jid) => {
    selectedJid = jid;
    if (refreshTimer) window.clearInterval(refreshTimer);
    window.setTimeout(refresh, 100);
    refreshTimer = window.setInterval(refresh, 2000);
  };

  const stop = () => {
    selectedJid = null;
    if (refreshTimer) window.clearInterval(refreshTimer);
    refreshTimer = null;
  };

  document.querySelectorAll(".agent-node[data-agent-runtime-jid]").forEach((node) => {
    node.addEventListener("click", () => start(node.dataset.agentRuntimeJid));
  });
  document.querySelectorAll("[data-agent-modal-close]").forEach((node) => {
    node.addEventListener("click", stop);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") stop();
  });
  document.getElementById("agent-modal-refresh")?.addEventListener("click", refresh);
  window.addEventListener("beforeunload", stop);
})();
