(() => {
  const panel = document.getElementById("incident-tools-panel");
  const grid = document.getElementById("incident-tools-grid");
  const empty = document.getElementById("incident-tools-empty");
  const distinctCount = document.getElementById("incident-tools-distinct-count");
  const callCount = document.getElementById("incident-tools-call-count");
  const updated = document.getElementById("incident-tools-updated");
  if (!panel || !grid) return;

  const incidentId = String(panel.dataset.incidentId || "").trim();
  if (!incidentId) return;

  let requestInFlight = false;

  const TOOL_KEYS = new Set(["tool", "tool_name", "toolName"]);
  const TOOL_LIST_KEYS = new Set(["tools", "tools_used", "used_tools", "tool_names"]);

  const humanizeRole = (value) => {
    if (!value) return "System";
    return String(value)
      .replace(/@.*$/, "")
      .replaceAll("-", " ")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  };

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

  const normalizeToolName = (value) => {
    const name = String(value || "").trim().replace(/^['"]|['"]$/g, "");
    if (!name || name === "none" || name === "null" || name === "—") return null;
    return name;
  };

  const addDelimitedTools = (text, target) => {
    let raw = String(text || "").trim();
    if (!raw) return;

    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) || (parsed && typeof parsed === "object")) {
        collectToolNames(parsed, target, "tools_used");
        return;
      }
      if (typeof parsed === "string" && parsed !== raw) raw = parsed.trim();
    } catch (_) {
      // Some persisted model results are Python-style stringified lists rather than JSON.
    }

    raw
      .replace(/^\s*[\[(]|[\])]\s*$/g, "")
      .split(",")
      .map((item) => normalizeToolName(item.trim().replace(/^['"]|['"]$/g, "")))
      .filter(Boolean)
      .forEach((name) => target.add(name));
  };

  const collectToolNames = (value, target, keyHint = "", depth = 0) => {
    if (depth > 10 || value === null || value === undefined) return;

    if (Array.isArray(value)) {
      value.forEach((item) => collectToolNames(item, target, keyHint, depth + 1));
      return;
    }

    if (typeof value === "object") {
      Object.entries(value).forEach(([key, item]) => {
        if (TOOL_KEYS.has(key) && typeof item === "string") {
          const name = normalizeToolName(item);
          if (name) target.add(name);
        } else if (TOOL_LIST_KEYS.has(key)) {
          if (typeof item === "string") addDelimitedTools(item, target);
          else collectToolNames(item, target, key, depth + 1);
        } else {
          collectToolNames(item, target, key, depth + 1);
        }
      });
      return;
    }

    if (typeof value !== "string") return;

    if (TOOL_KEYS.has(keyHint)) {
      const name = normalizeToolName(value);
      if (name) target.add(name);
      return;
    }

    if (TOOL_LIST_KEYS.has(keyHint)) {
      addDelimitedTools(value, target);
      return;
    }

    // Backfill tools from stringified structured payloads such as model summaries.
    const matches = value.match(/\b(?:apm_mcp|mcp|rag)_[A-Za-z0-9_.:-]+\b/g) || [];
    matches.forEach((match) => {
      const name = normalizeToolName(match);
      if (name) target.add(name);
    });
  };

  const classifyTool = (name) => {
    const normalized = String(name || "").toLowerCase();
    if (normalized.includes("mcp")) return "MCP";
    if (normalized.includes("rag") || normalized.includes("knowledge")) return "RAG";
    return "TOOL";
  };

  const extractTools = (incident) => {
    const records = new Map();
    const timeline = Array.isArray(incident?.timeline) ? incident.timeline : [];

    timeline.forEach((event) => {
      const names = new Set();
      collectToolNames(event, names);
      if (!names.size) return;

      const role = humanizeRole(event.agent_role || event.agent_jid || event.called_by);
      const timestamp = event.timestamp || event.created_at || null;
      const action = event.action || event.event_type || "Agent activity";

      names.forEach((name) => {
        const current = records.get(name) || {
          name,
          calls: 0,
          agents: new Set(),
          actions: new Set(),
          firstTimestamp: null,
          lastTimestamp: null,
        };
        current.calls += 1;
        current.agents.add(role);
        current.actions.add(action);

        if (timestamp) {
          if (!current.firstTimestamp || String(timestamp) < String(current.firstTimestamp)) {
            current.firstTimestamp = timestamp;
          }
          if (!current.lastTimestamp || String(timestamp) > String(current.lastTimestamp)) {
            current.lastTimestamp = timestamp;
          }
        }
        records.set(name, current);
      });
    });

    return [...records.values()].sort((a, b) => {
      if (b.calls !== a.calls) return b.calls - a.calls;
      return a.name.localeCompare(b.name);
    });
  };

  const createText = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text;
    return node;
  };

  const render = (incident) => {
    const tools = extractTools(incident);
    grid.replaceChildren();

    const totalCalls = tools.reduce((sum, tool) => sum + tool.calls, 0);
    if (distinctCount) distinctCount.textContent = String(tools.length);
    if (callCount) callCount.textContent = String(totalCalls);
    if (updated) updated.textContent = formatTimestamp(incident?.updated_at);

    if (!tools.length) {
      if (empty) {
        empty.hidden = false;
        const title = empty.querySelector("strong");
        const copy = empty.querySelector("p");
        if (title) title.textContent = "No diagnostic tool calls recorded yet.";
        if (copy) copy.textContent = "The list will populate automatically when ReAct or RAG tool usage is persisted in the incident timeline.";
      }
      grid.hidden = true;
      return;
    }

    if (empty) empty.hidden = true;
    grid.hidden = false;

    tools.forEach((tool) => {
      const card = document.createElement("article");
      card.className = "incident-tool-card";

      const header = document.createElement("div");
      header.className = "incident-tool-card-header";
      const title = document.createElement("div");
      title.className = "incident-tool-title";
      title.appendChild(createText("span", "incident-tool-kind", classifyTool(tool.name)));
      title.appendChild(createText("strong", "", tool.name));
      header.appendChild(title);
      header.appendChild(createText("span", "incident-tool-calls", `${tool.calls} ${tool.calls === 1 ? "use" : "uses"}`));
      card.appendChild(header);

      const facts = document.createElement("div");
      facts.className = "incident-tool-facts";

      const agents = document.createElement("div");
      agents.appendChild(createText("span", "", "Used by"));
      agents.appendChild(createText("strong", "", [...tool.agents].join(", ") || "System"));
      facts.appendChild(agents);

      const lastUse = document.createElement("div");
      lastUse.appendChild(createText("span", "", "Last observed"));
      lastUse.appendChild(createText("strong", "", formatTimestamp(tool.lastTimestamp)));
      facts.appendChild(lastUse);

      card.appendChild(facts);

      if (tool.actions.size) {
        const actions = document.createElement("div");
        actions.className = "incident-tool-actions";
        actions.appendChild(createText("span", "", "Observed in"));
        actions.appendChild(createText("p", "", [...tool.actions].slice(0, 4).join(" · ")));
        card.appendChild(actions);
      }

      grid.appendChild(card);
    });
  };

  const refresh = async () => {
    if (requestInFlight || document.hidden) return;
    requestInFlight = true;
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}`, {
        cache: "no-store",
      });
      if (!response.ok) return;
      render(await response.json());
    } catch (_) {
      // Keep the last rendered tool history on transient connectivity errors.
    } finally {
      requestInFlight = false;
    }
  };

  refresh();
  window.setInterval(refresh, 3000);
})();
