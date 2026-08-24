(() => {
  const network = document.getElementById("agent-network");
  if (!network) return;

  const STATES = ["IDLE", "WORKING", "WAITING"];
  const DETAIL_LABELS = {
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

  let latestPayload = null;
  let applying = false;
  let requestInFlight = false;

  const normalizeState = (value) => {
    const normalized = String(value || "IDLE").trim().toUpperCase();
    return STATES.includes(normalized) ? normalized : "IDLE";
  };

  const labelFor = (state, detail) => {
    const normalized = normalizeState(state);
    if (normalized === "IDLE") return "IDLE";
    const key = String(detail || "").trim().toLowerCase();
    if (key.startsWith("collaborating_with_")) return "COLLABORATING";
    return DETAIL_LABELS[key] || normalized;
  };

  const findNode = (jid) => Array.from(
    network.querySelectorAll(".agent-node[data-agent-runtime-jid]"),
  ).find((node) => String(node.dataset.agentRuntimeJid || "").toLowerCase() === String(jid || "").toLowerCase());

  const applyMember = (member) => {
    const node = findNode(member?.jid);
    if (!node) return;

    const state = normalizeState(member.activity);
    const detail = state === "IDLE" ? "" : String(member.activity_detail || "");
    const label = labelFor(state, detail);

    if (node.dataset.agentActivityState !== state) node.dataset.agentActivityState = state;
    if ((node.dataset.agentActivityDetail || "") !== detail) node.dataset.agentActivityDetail = detail;

    STATES.forEach((candidate) => {
      node.classList.toggle(candidate.toLowerCase(), candidate === state);
    });

    const pill = node.querySelector("[data-agent-activity]");
    if (pill) {
      if (pill.textContent !== label) pill.textContent = label;
      STATES.forEach((candidate) => {
        pill.classList.toggle(candidate.toLowerCase(), candidate === state);
      });
    }
  };

  const applyModalState = (members) => {
    const modal = document.getElementById("agent-observability-modal");
    if (!modal || modal.hidden) return;

    const jid = document.getElementById("agent-modal-jid")?.textContent?.trim().toLowerCase();
    if (!jid) return;

    const member = members.find((item) => {
      const runtimeJid = String(item?.jid || "").toLowerCase();
      return runtimeJid === jid || runtimeJid.replaceAll("_", "-") === jid;
    });
    if (!member) return;

    const state = normalizeState(member.activity);
    const detail = state === "IDLE" ? "" : member.activity_detail;
    const modalState = document.getElementById("agent-modal-state");
    if (modalState) {
      modalState.textContent = labelFor(state, detail);
      STATES.forEach((candidate) => {
        modalState.classList.toggle(candidate.toLowerCase(), candidate === state);
      });
    }

    const currentIncident = document.getElementById("agent-current-incident");
    if (currentIncident && state === "IDLE" && !member.activity_incident_id) {
      currentIncident.textContent = "—";
    }
  };

  const applyPayload = () => {
    const team = latestPayload?.team;
    if (!team) return;

    applying = true;
    try {
      const teamState = normalizeState(team.state);
      const stateNode = document.getElementById("team-activity-state");
      if (stateNode) {
        if (stateNode.textContent !== teamState) stateNode.textContent = teamState;
        STATES.forEach((candidate) => {
          stateNode.classList.toggle(candidate.toLowerCase(), candidate === teamState);
        });
      }

      STATES.forEach((candidate) => {
        network.classList.toggle(candidate.toLowerCase(), candidate === teamState);
      });

      const incidentNode = document.getElementById("team-active-incidents");
      if (incidentNode) {
        const count = Number(team.active_incidents || 0);
        const suffix = count === 1 ? "incident" : "incidents";
        const text = `${count} ${suffix} with active agent context`;
        if (incidentNode.textContent !== text) incidentNode.textContent = text;
      }

      const members = Array.isArray(team.members) ? team.members : [];
      members.forEach(applyMember);
      applyModalState(members);
    } finally {
      applying = false;
    }
  };

  const refresh = async () => {
    if (requestInFlight) return;
    requestInFlight = true;
    try {
      const response = await fetch("/api/overview", { cache: "no-store" });
      if (!response.ok) return;
      latestPayload = await response.json();
      applyPayload();
    } catch (_) {
      // Preserve the last authoritative runtime snapshot on transient errors.
    } finally {
      requestInFlight = false;
    }
  };

  // Agent health WebSockets are authoritative for presence only. Some health payloads can
  // briefly carry the previous activity detail after an incident is completed. The REST
  // runtime snapshot is authoritative for activity, so re-apply it whenever another script
  // mutates activity classes or labels.
  const observer = new MutationObserver(() => {
    if (applying || !latestPayload) return;
    queueMicrotask(applyPayload);
  });

  observer.observe(network, {
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "data-agent-activity-state", "data-agent-activity-detail"],
    childList: true,
    characterData: true,
  });

  refresh();
  window.setInterval(refresh, 2000);
})();
