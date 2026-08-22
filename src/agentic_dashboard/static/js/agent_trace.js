(() => {
  const modal = document.getElementById("agent-observability-modal");
  const eventList = document.getElementById("agent-event-list");
  const detailPanel = document.getElementById("agent-event-detail");
  const detailEmpty = document.getElementById("agent-event-detail-empty");
  const empty = document.getElementById("agent-log-empty");
  const loading = document.getElementById("agent-log-loading");
  const eventCount = document.getElementById("agent-event-count");
  const visibleCount = document.getElementById("agent-log-visible-count");
  const lastActivity = document.getElementById("agent-last-activity");
  const currentIncident = document.getElementById("agent-current-incident");
  const searchInput = document.getElementById("agent-log-search");
  const statusFilter = document.getElementById("agent-log-status-filter");
  const currentOnly = document.getElementById("agent-log-current-only");
  if (!modal || !eventList || !detailPanel) return;

  let selectedJid = null;
  let selectedEventKey = null;
  let refreshTimer = null;
  let requestInFlight = false;
  let eventsCache = [];
  let currentIncidentId = null;

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

  const formatTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  };

  const formatDate = (value) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
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

  const compactValue = (value) => {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    try {
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  };

  const eventKey = (event, index) => event.event_id || event.id || event.trace_id || [
    event.timestamp || "",
    event.action || event.event_type || "",
    event.incident_id || "",
    event.called_by || event.agent_role || "",
    compactValue(event.outcome),
  ].join("::") || String(index);

  const eventAction = (event) => event.action || event.event_type || "agent_activity";
  const eventStatus = (event) => event.status || (event.event_type === "AGENT_EXECUTION_TRACE" ? "LIVE" : "RECORDED");

  const createText = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text;
    return node;
  };

  const createBadge = (text, variant = "neutral") => createText("span", `agent-event-badge ${variant}`, text || "—");

  const statusVariant = (status) => {
    const normalized = String(status || "").toUpperCase();
    if (normalized.includes("LIVE") || normalized.includes("WORKING")) return "live";
    if (normalized.includes("FAIL") || normalized.includes("ERROR") || normalized.includes("REJECT")) return "danger";
    if (normalized.includes("WAIT") || normalized.includes("PENDING")) return "warning";
    if (normalized.includes("TRIAGED") || normalized.includes("ACCEPT") || normalized.includes("RESOLVED")) return "success";
    return "neutral";
  };

  const createIncidentLink = (incidentId) => {
    if (!incidentId) return createText("span", "agent-detail-value", "—");
    const link = document.createElement("a");
    link.className = "agent-detail-link";
    link.href = `/incidents/${encodeURIComponent(incidentId)}`;
    link.textContent = incidentId;
    return link;
  };

  const appendFact = (container, label, value, options = {}) => {
    const fact = document.createElement("div");
    fact.className = "agent-detail-fact";
    fact.appendChild(createText("span", "agent-detail-label", label));
    if (options.node) {
      fact.appendChild(options.node);
    } else {
      fact.appendChild(createText("strong", "agent-detail-value", compactValue(value)));
    }
    container.appendChild(fact);
  };

  const appendSection = (container, title, value, className = "") => {
    if (value === undefined || value === null || value === "") return;
    const section = document.createElement("section");
    section.className = `agent-detail-section ${className}`.trim();
    section.appendChild(createText("h4", "", title));
    section.appendChild(createText("div", "agent-detail-section-content", compactValue(value)));
    container.appendChild(section);
  };

  const appendDisclosure = (container, label, value, open = false) => {
    if (value === undefined || value === null || value === "") return;
    const disclosure = document.createElement("details");
    disclosure.className = "agent-detail-disclosure";
    disclosure.open = open;
    const summary = document.createElement("summary");
    summary.textContent = label;
    const pre = document.createElement("pre");
    pre.textContent = stringify(value);
    disclosure.append(summary, pre);
    container.appendChild(disclosure);
  };

  const renderDetail = (event, key) => {
    selectedEventKey = key;
    detailPanel.replaceChildren();
    detailPanel.classList.remove("is-empty");

    const header = document.createElement("header");
    header.className = "agent-event-detail-header";
    const heading = document.createElement("div");
    heading.className = "agent-event-detail-heading";
    heading.appendChild(createText("span", "eyebrow", "SELECTED EVENT"));
    heading.appendChild(createText("h3", "", eventAction(event)));
    heading.appendChild(createText("p", "", formatTimestamp(event.timestamp)));
    const badges = document.createElement("div");
    badges.className = "agent-event-detail-badges";
    const status = eventStatus(event);
    badges.appendChild(createBadge(status, statusVariant(status)));
    if (event.event_type === "AGENT_EXECUTION_TRACE" && String(status).toUpperCase() !== "LIVE") {
      badges.appendChild(createBadge("LIVE TRACE", "live"));
    }
    header.append(heading, badges);
    detailPanel.appendChild(header);

    const facts = document.createElement("div");
    facts.className = "agent-detail-facts";
    appendFact(facts, "Called by", event.called_by || event.agent_role || "system");
    appendFact(facts, "Incident", null, { node: createIncidentLink(event.incident_id) });
    appendFact(facts, "Tool", event.tool || "—");
    appendFact(facts, "Outcome", event.outcome || event.status || "—");
    detailPanel.appendChild(facts);

    appendSection(detailPanel, "Operational rationale", event.reason || "No rationale recorded for this event.", "rationale");

    if (event.tool || event.outcome) {
      const toolSection = document.createElement("section");
      toolSection.className = "agent-detail-section tool-outcome";
      toolSection.appendChild(createText("h4", "", "Tool / outcome"));
      const toolGrid = document.createElement("div");
      toolGrid.className = "agent-tool-outcome-grid";
      appendFact(toolGrid, "Tool", event.tool || "—");
      appendFact(toolGrid, "Result", event.outcome || event.status || "—");
      toolSection.appendChild(toolGrid);
      detailPanel.appendChild(toolSection);
    }

    const details = event.details && typeof event.details === "object" ? event.details : null;
    if (details && Object.keys(details).length) {
      const hasObservationViews = Object.prototype.hasOwnProperty.call(details, "raw_observation")
        || Object.prototype.hasOwnProperty.call(details, "reasoning_observation");

      if (hasObservationViews) {
        appendDisclosure(detailPanel, "Reasoning observation", details.reasoning_observation, true);
        appendDisclosure(
          detailPanel,
          event.action === "rag_retrieval" ? "Complete retrieved knowledge" : "Complete MCP output",
          details.raw_observation,
        );
        const metadata = { ...details };
        delete metadata.reasoning_observation;
        delete metadata.raw_observation;
        if (Object.keys(metadata).length) appendDisclosure(detailPanel, "Trace metadata", metadata);
      } else {
        appendDisclosure(
          detailPanel,
          event.action === "rag_retrieval" ? "Retrieved knowledge" : "Event payload",
          details,
          true,
        );
      }
    }

    if (typeof event.outcome === "object" && event.outcome !== null) {
      appendDisclosure(detailPanel, "Outcome payload", event.outcome, !details);
    }
  };

  const buildSearchText = (event) => [
    eventAction(event),
    eventStatus(event),
    event.called_by,
    event.agent_role,
    event.reason,
    event.tool,
    compactValue(event.outcome),
    event.incident_id,
    stringify(event.details),
  ].filter(Boolean).join(" ").toLowerCase();

  const filteredEvents = () => {
    const query = String(searchInput?.value || "").trim().toLowerCase();
    const status = String(statusFilter?.value || "").trim().toLowerCase();
    return eventsCache
      .map((event, index) => ({ event, index, key: eventKey(event, index) }))
      .filter(({ event }) => {
        if (query && !buildSearchText(event).includes(query)) return false;
        if (status && String(eventStatus(event)).toLowerCase() !== status) return false;
        if (currentOnly?.checked && currentIncidentId && event.incident_id !== currentIncidentId) return false;
        return true;
      });
  };

  const updateStatusOptions = () => {
    if (!statusFilter) return;
    const previous = statusFilter.value;
    const values = [...new Set(eventsCache.map((event) => eventStatus(event)).filter(Boolean))]
      .sort((a, b) => String(a).localeCompare(String(b)));
    statusFilter.replaceChildren();
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "All statuses";
    statusFilter.appendChild(all);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      statusFilter.appendChild(option);
    });
    statusFilter.value = values.includes(previous) ? previous : "";
  };

  const renderList = () => {
    const scrollTop = eventList.scrollTop;
    const items = filteredEvents();
    eventList.replaceChildren();
    if (visibleCount) visibleCount.textContent = String(items.length);
    if (empty) empty.hidden = items.length > 0;

    if (!items.length) {
      detailPanel.replaceChildren();
      detailPanel.classList.add("is-empty");
      const placeholder = document.createElement("div");
      placeholder.className = "agent-event-detail-empty";
      placeholder.appendChild(createText("strong", "", eventsCache.length ? "No events match the current filters." : "Select an event to inspect it."));
      placeholder.appendChild(createText("p", "", eventsCache.length ? "Change the search or filter settings to restore the timeline." : "The complete operational rationale and payload will appear here."));
      detailPanel.appendChild(placeholder);
      return;
    }

    const active = items.find((item) => item.key === selectedEventKey) || items[0];
    selectedEventKey = active.key;

    items.forEach((item) => {
      const { event, key } = item;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `agent-event-item${key === selectedEventKey ? " selected" : ""}`;
      button.dataset.eventKey = key;

      const time = document.createElement("div");
      time.className = "agent-event-time";
      time.appendChild(createText("strong", "", formatTime(event.timestamp)));
      time.appendChild(createText("span", "", formatDate(event.timestamp)));

      const content = document.createElement("div");
      content.className = "agent-event-copy";
      const top = document.createElement("div");
      top.className = "agent-event-item-top";
      top.appendChild(createText("strong", "agent-event-action", eventAction(event)));
      const status = eventStatus(event);
      top.appendChild(createBadge(status, statusVariant(status)));
      content.appendChild(top);

      content.appendChild(createText("p", "agent-event-preview", event.reason || compactValue(event.outcome) || "No rationale recorded."));

      const meta = document.createElement("div");
      meta.className = "agent-event-meta";
      meta.appendChild(createText("span", "", event.called_by || event.agent_role || "system"));
      if (event.incident_id) meta.appendChild(createText("span", "incident", event.incident_id));
      content.appendChild(meta);

      button.append(time, content);
      button.addEventListener("click", () => {
        selectedEventKey = key;
        eventList.querySelectorAll(".agent-event-item").forEach((node) => {
          node.classList.toggle("selected", node.dataset.eventKey === key);
        });
        renderDetail(event, key);
      });
      eventList.appendChild(button);
    });

    eventList.scrollTop = scrollTop;
    renderDetail(active.event, active.key);
  };

  const render = (payload) => {
    eventsCache = Array.isArray(payload?.events) ? payload.events : [];
    currentIncidentId = payload?.agent?.activity_incident_id
      || eventsCache.find((event) => event.incident_id)?.incident_id
      || null;

    if (eventCount) eventCount.textContent = String(eventsCache.length);
    if (lastActivity) lastActivity.textContent = eventsCache.length ? formatTimestamp(eventsCache[0].timestamp) : "—";
    if (currentIncident) currentIncident.textContent = currentIncidentId || "—";
    if (currentOnly) currentOnly.disabled = !currentIncidentId;

    updateStatusOptions();
    renderList();
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
    selectedEventKey = null;
    eventsCache = [];
    currentIncidentId = null;
    if (searchInput) searchInput.value = "";
    if (statusFilter) statusFilter.value = "";
    if (currentOnly) currentOnly.checked = false;
    if (refreshTimer) window.clearInterval(refreshTimer);
    window.setTimeout(refresh, 80);
    refreshTimer = window.setInterval(refresh, 2000);
  };

  const stop = () => {
    selectedJid = null;
    selectedEventKey = null;
    eventsCache = [];
    currentIncidentId = null;
    if (refreshTimer) window.clearInterval(refreshTimer);
    refreshTimer = null;
  };

  searchInput?.addEventListener("input", renderList);
  statusFilter?.addEventListener("change", renderList);
  currentOnly?.addEventListener("change", renderList);

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
