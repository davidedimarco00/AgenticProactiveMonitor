(() => {
  const panel = document.getElementById("workflow-panel");
  const anomalyInboxBody = document.getElementById("anomaly-inbox-body");
  const queueDepthDisplay = document.getElementById("workflow-queue-depth");
  if (!panel && !anomalyInboxBody && !queueDepthDisplay) return;

  const byId = (id) => document.getElementById(id);
  const backendAnomalyApi = `${window.location.protocol}//${window.location.hostname}:8082/api/v1/anomalies`;
  let durableWaitingCount = null;

  const humanizeRole = (value) => {
    if (!value) return "—";
    return String(value)
      .replaceAll("_", " ")
      .replaceAll("-", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  };

  const humanizeStatus = (value) => String(value || "—")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

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

  const formatConfidence = (value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    const percentage = numeric <= 1 ? numeric * 100 : numeric;
    return `${percentage.toFixed(0)}%`;
  };

  const formatGrade = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(3) : "—";
  };

  const compactStructured = (value, fallback = "—") => {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    if (typeof value === "object") {
      const preferred = value.summary || value.message || value.reason || value.root_cause || value.status;
      if (preferred) return String(preferred);
      try {
        const serialized = JSON.stringify(value);
        return serialized.length > 260 ? `${serialized.slice(0, 257)}…` : serialized;
      } catch (_) {
        return fallback;
      }
    }
    return String(value);
  };

  const appendTextCell = (row, value, className = "") => {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value;
    row.appendChild(cell);
    return cell;
  };

  const replaceIncidentNode = (incidentId) => {
    const current = byId("workflow-incident");
    if (!current) return;

    if (incidentId) {
      if (current.tagName === "A") {
        current.href = `/incidents/${encodeURIComponent(incidentId)}`;
        current.innerHTML = `<strong>${incidentId}</strong>`;
        return;
      }
      const link = document.createElement("a");
      link.id = "workflow-incident";
      link.href = `/incidents/${encodeURIComponent(incidentId)}`;
      const strong = document.createElement("strong");
      strong.textContent = incidentId;
      link.appendChild(strong);
      current.replaceWith(link);
      return;
    }

    if (current.tagName === "A") {
      const strong = document.createElement("strong");
      strong.id = "workflow-incident";
      strong.textContent = "—";
      current.replaceWith(strong);
    } else {
      current.textContent = "—";
    }
  };

  const effectiveState = (workflow) => {
    const state = String(workflow?.state || "IDLE").toUpperCase();
    if (
      state === "IDLE" &&
      workflow?.active_incident_id &&
      workflow?.task &&
      !workflow?.active_anomaly
    ) {
      return "RECOVERY";
    }
    return state;
  };

  const setStatusPill = (node, status) => {
    if (!node) return;
    const normalized = String(status || "NO ACTIVE INCIDENT").trim().toUpperCase();
    node.className = `status-pill status-${normalized.toLowerCase().replaceAll("_", "-").replaceAll(" ", "-")}`;
    node.textContent = normalized.replaceAll("_", " ");
  };

  const progressStage = (workflow, incident) => {
    if (!incident || !Object.keys(incident).length) return workflow?.active_anomaly ? 0 : -1;

    const status = String(incident.status || "NEW").toUpperCase();
    const agentic = incident.agentic || {};
    const task = workflow?.task || null;
    const reviewState = String(agentic.review_state || "").toUpperCase();

    if (["DIAGNOSED", "OPERATOR_ACTION_REQUIRED", "RESOLVED", "CLOSED"].includes(status)) return 4;
    if (status === "UNDER_ANALYSIS") {
      if (reviewState && reviewState !== "NOT_STARTED") return 3;
      if (String(task?.state || "").toUpperCase() === "COMPLETED") return 3;
      return 2;
    }
    if (status === "TRIAGED") return 2;
    if (status === "TAKEN_IN_CHARGE") return 1;
    return 0;
  };

  const renderDiagnosisProgress = (workflow, incident) => {
    const progress = byId("diagnosis-progress");
    if (!progress) return;

    const current = progressStage(workflow, incident);
    progress.querySelectorAll(".diagnosis-stage").forEach((stage) => {
      const index = Number(stage.dataset.stageIndex || 0);
      stage.classList.toggle("done", current >= 0 && index < current);
      stage.classList.toggle("current", index === current);
    });

    const label = byId("diagnosis-progress-label");
    if (label) label.textContent = workflow?.phase || (current < 0 ? "Waiting for anomaly" : "Anomaly intake");
  };

  const renderLiveDiagnosis = (workflow, team) => {
    if (!byId("diagnosis-live-card")) return;

    const incident = workflow?.active_incident || null;
    const diagnosis = incident?.diagnosis || {};
    const agentic = incident?.agentic || {};
    const timeline = Array.isArray(incident?.timeline) ? incident.timeline : [];
    const latest = timeline.length ? timeline[timeline.length - 1] : null;
    const state = effectiveState(workflow);

    const status = incident?.status || (workflow?.active_anomaly ? "STARTING" : "NO ACTIVE INCIDENT");
    setStatusPill(byId("diagnosis-live-status"), status);

    const summary = byId("diagnosis-live-summary");
    if (summary) {
      summary.textContent = incident
        ? diagnosis.summary || "The agents are still collecting and correlating evidence. No stable diagnosis has been persisted yet."
        : workflow?.active_anomaly
          ? "The anomaly has been admitted to the workflow. Incident creation and Technical Lead triage are starting."
          : "No anomaly is currently being processed. The live diagnostic summary will appear here when the team takes an anomaly in charge.";
    }

    const confidence = byId("diagnosis-live-confidence");
    if (confidence) confidence.textContent = formatConfidence(diagnosis.confidence);

    const evidence = byId("diagnosis-live-evidence");
    if (evidence) evidence.textContent = String(Array.isArray(diagnosis.evidence) ? diagnosis.evidence.length : 0);

    const workingMembers = Array.isArray(team?.members)
      ? team.members.filter((member) => String(member.activity || "").toUpperCase() === "WORKING")
      : [];
    const waitingMembers = Array.isArray(team?.members)
      ? team.members.filter((member) => String(member.activity || "").toUpperCase() === "WAITING")
      : [];
    const agents = byId("diagnosis-live-agents");
    if (agents) {
      if (workingMembers.length) {
        agents.textContent = workingMembers.map((member) => member.name || humanizeRole(member.backend_role || member.key)).join(", ");
      } else if (waitingMembers.length) {
        agents.textContent = `${waitingMembers.map((member) => member.name || humanizeRole(member.backend_role || member.key)).join(", ")} waiting`;
      } else {
        agents.textContent = workflow?.assigned_to ? humanizeRole(workflow.assigned_to) : "Idle";
      }
    }

    const updated = byId("diagnosis-live-updated");
    if (updated) updated.textContent = formatTimestamp(incident?.updated_at || latest?.timestamp);

    const rootCause = byId("diagnosis-live-root-cause");
    if (rootCause) rootCause.textContent = diagnosis.root_cause || "No root-cause candidate has been confirmed yet.";

    const latestTitle = byId("diagnosis-latest-title");
    const latestCopy = byId("diagnosis-latest-copy");
    const latestTime = byId("diagnosis-latest-time");
    if (latest) {
      if (latestTitle) {
        const actor = latest.agent_role || latest.agent_jid || latest.called_by;
        latestTitle.textContent = `${latest.action || latest.event_type || "Agent activity"}${actor ? ` · ${humanizeRole(actor)}` : ""}`;
      }
      if (latestCopy) {
        latestCopy.textContent = latest.reason
          || latest.description
          || compactStructured(latest.outcome, "Agent activity recorded without an operator summary.");
      }
      if (latestTime) latestTime.textContent = formatTimestamp(latest.timestamp || latest.created_at);
    } else {
      if (latestTitle) latestTitle.textContent = state === "IDLE" ? "Waiting for agent activity" : "Investigation starting";
      if (latestCopy) latestCopy.textContent = state === "IDLE"
        ? "The latest agent observation will appear here as the investigation progresses."
        : "The team has not persisted a diagnostic event for this incident yet.";
      if (latestTime) latestTime.textContent = "—";
    }

    const collaboration = byId("diagnosis-live-collaboration");
    if (collaboration) {
      const peerState = String(agentic.peer_collaboration_state || "").trim();
      collaboration.textContent = peerState
        ? humanizeStatus(peerState)
        : agentic.support_requested
          ? "Support requested"
          : workingMembers.length > 1
            ? "Collaborative specialist investigation"
            : workflow?.assigned_to
              ? `Primary investigator: ${humanizeRole(workflow.assigned_to)}`
              : "Primary investigator workflow";
    }

    const incidentLink = byId("diagnosis-open-incident");
    if (incidentLink) {
      if (workflow?.active_incident_id) {
        incidentLink.href = `/incidents/${encodeURIComponent(workflow.active_incident_id)}`;
        incidentLink.classList.remove("is-disabled");
        incidentLink.removeAttribute("aria-disabled");
      } else {
        incidentLink.href = "#";
        incidentLink.classList.add("is-disabled");
        incidentLink.setAttribute("aria-disabled", "true");
      }
    }

    renderDiagnosisProgress(workflow, incident);
  };

  const renderWorkflow = (workflow, team) => {
    if (!panel || !workflow) return;

    const state = effectiveState(workflow);
    panel.dataset.workflowState = state;

    const stateNode = byId("workflow-state");
    if (stateNode) stateNode.textContent = state;

    const dot = panel.querySelector(".workflow-state-dot");
    if (dot) {
      dot.classList.remove("state-idle", "state-processing", "state-queued");
      dot.classList.add(state === "RECOVERY" ? "state-queued" : `state-${state.toLowerCase()}`);
    }

    const phase = byId("workflow-phase");
    if (phase) phase.textContent = workflow.phase || "ANOMALY INTAKE";

    replaceIncidentNode(workflow.active_incident_id || null);

    const anomaly = workflow.active_anomaly || null;
    const anomalyNode = byId("workflow-anomaly");
    const resultNode = byId("workflow-result-id");
    if (anomalyNode) anomalyNode.textContent = anomaly?.detector_name || anomaly?.detector_id || "No active anomaly";
    if (resultNode) {
      resultNode.textContent = anomaly?.result_id || (
        state === "RECOVERY"
          ? "Persisted workflow from an earlier runtime"
          : "The team is ready for the next anomaly"
      );
    }

    const assignee = byId("workflow-assignee");
    if (assignee) assignee.textContent = humanizeRole(workflow.assigned_to);

    const task = workflow.task || null;
    const taskState = byId("workflow-task-state");
    const taskAttempt = byId("workflow-task-attempt");
    if (taskState) taskState.textContent = task?.state || "—";
    if (taskAttempt) {
      taskAttempt.textContent = task
        ? `Attempt ${task.attempt ?? 0} / ${task.max_attempts ?? 0}`
        : "Created after Technical Lead triage";
    }

    const runtimeDepth = Math.max(Number(workflow.queue_depth || 0), 0);
    const queueDepth = durableWaitingCount === null ? runtimeDepth : durableWaitingCount;
    const depthNode = byId("workflow-queue-depth");
    if (depthNode) depthNode.textContent = String(queueDepth);

    const error = byId("workflow-error");
    const errorMessage = byId("workflow-error-message");
    if (error) error.hidden = !workflow.last_error;
    if (errorMessage) errorMessage.textContent = workflow.last_error || "";

    renderLiveDiagnosis(workflow, team);
  };

  const syncDurableQueueCount = (summary) => {
    durableWaitingCount = Math.max(Number(summary?.waiting || 0), 0);
    const depthNode = byId("workflow-queue-depth");
    if (depthNode) depthNode.textContent = String(durableWaitingCount);
  };

  const dismissWaitingAnomaly = async (anomaly, button) => {
    const label = anomaly.detector_name || anomaly.detector_id || anomaly.result_id || "this anomaly";
    const confirmed = window.confirm(
      `Remove ${label} from the waiting queue as a false positive?\n\nThis action is allowed only before the agentic team takes the anomaly in charge.`,
    );
    if (!confirmed) return;

    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(
        `${backendAnomalyApi}/${encodeURIComponent(anomaly.anomaly_key)}`,
        { method: "DELETE", cache: "no-store" },
      );
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          detail = payload.detail || payload.message || detail;
        } catch (_) {
          // Keep HTTP status fallback.
        }
        throw new Error(detail);
      }

      await refreshAnomalyInbox();
      await refreshWorkflow();
    } catch (error) {
      window.alert(`Could not remove the anomaly: ${error.message || error}`);
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  };

  const renderAnomalyInbox = (payload) => {
    const anomalies = Array.isArray(payload?.anomalies) ? payload.anomalies : [];
    const summary = payload?.summary || {};
    const body = byId("anomaly-inbox-body");
    const table = byId("anomaly-inbox-table");
    const empty = byId("anomaly-inbox-empty");

    const waitingCount = byId("anomaly-inbox-waiting-count");
    const processingCount = byId("anomaly-inbox-processing-count");
    const recoveryCount = byId("anomaly-inbox-recovery-count");
    if (waitingCount) waitingCount.textContent = String(Number(summary.waiting || 0));
    if (processingCount) processingCount.textContent = String(Number(summary.processing || 0));
    if (recoveryCount) recoveryCount.textContent = String(Number(summary.recovery || 0));
    syncDurableQueueCount(summary);

    if (!body || !table || !empty) return;
    body.replaceChildren();

    if (!anomalies.length) {
      table.hidden = true;
      empty.hidden = false;
      return;
    }

    table.hidden = false;
    empty.hidden = true;

    anomalies.forEach((anomaly, index) => {
      const row = document.createElement("tr");
      appendTextCell(row, String(index + 1), "muted nowrap");

      const detectorCell = appendTextCell(row, "");
      const detectorStrong = document.createElement("strong");
      detectorStrong.textContent = anomaly.detector_name || anomaly.detector_id || "Unknown detector";
      detectorCell.appendChild(detectorStrong);

      if (anomaly.detector_name && anomaly.detector_id) {
        const detectorId = document.createElement("small");
        detectorId.textContent = anomaly.detector_id;
        detectorCell.appendChild(detectorId);
      }

      if (anomaly.detector_description) {
        const description = document.createElement("small");
        description.className = "anomaly-detector-description";
        description.textContent = anomaly.detector_description;
        detectorCell.appendChild(description);
      }

      const resultCell = appendTextCell(row, "");
      const resultStrong = document.createElement("strong");
      resultStrong.textContent = anomaly.result_id || "—";
      resultCell.appendChild(resultStrong);
      if (anomaly.result_index) {
        const indexLabel = document.createElement("small");
        indexLabel.textContent = anomaly.result_index;
        resultCell.appendChild(indexLabel);
      }

      appendTextCell(row, formatGrade(anomaly.anomaly_grade), "nowrap");
      appendTextCell(row, formatConfidence(anomaly.confidence), "nowrap");
      appendTextCell(row, formatTimestamp(anomaly.received_at), "muted nowrap");

      const stateCell = appendTextCell(row, "");
      const state = String(anomaly.state || "WAITING").toUpperCase();
      const statePill = document.createElement("span");
      statePill.className = `status-pill status-${state.toLowerCase().replaceAll("_", "-")}`;
      statePill.textContent = state.replaceAll("_", " ");
      stateCell.appendChild(statePill);

      const actionCell = appendTextCell(row, "", "anomaly-action-cell");
      if (!anomaly.incident_id) {
        const dismissButton = document.createElement("button");
        dismissButton.type = "button";
        dismissButton.className = "anomaly-dismiss-button";
        dismissButton.title = "Dismiss as false positive";
        dismissButton.setAttribute("aria-label", `Dismiss ${anomaly.detector_name || anomaly.detector_id || "anomaly"} as false positive`);
        dismissButton.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-1 11H8L7 9Zm3 2v6h2v-6h-2Zm4 0v6h2v-6h-2Z"></path>
          </svg>
        `;
        dismissButton.addEventListener("click", () => dismissWaitingAnomaly(anomaly, dismissButton));
        actionCell.appendChild(dismissButton);
      } else {
        const owned = document.createElement("small");
        owned.className = "muted";
        owned.textContent = `Owned by ${anomaly.incident_id}`;
        actionCell.appendChild(owned);
      }

      body.appendChild(row);
    });
  };

  const refreshWorkflow = async () => {
    if (!panel) return;
    try {
      const response = await fetch("/api/overview", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      renderWorkflow(payload.workflow, payload.team);
    } catch (_) {
      // Preserve the last server-rendered workflow snapshot on transient errors.
    }
  };

  const refreshAnomalyInbox = async () => {
    if (!anomalyInboxBody && !queueDepthDisplay) return;
    const limit = anomalyInboxBody ? 4096 : 1;
    try {
      const response = await fetch(
        `/api/anomalies?state=WAITING&limit=${limit}&ascending=true`,
        { cache: "no-store" },
      );
      if (!response.ok) return;
      renderAnomalyInbox(await response.json());
    } catch (_) {
      // Preserve the last durable-inbox rendering on transient backend errors.
    }
  };

  refreshWorkflow();
  refreshAnomalyInbox();
  if (panel) window.setInterval(refreshWorkflow, 4000);
  if (anomalyInboxBody || queueDepthDisplay) window.setInterval(refreshAnomalyInbox, 8000);
})();