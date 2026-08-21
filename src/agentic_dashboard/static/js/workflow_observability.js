(() => {
  const panel = document.getElementById("workflow-panel");
  if (!panel) return;

  const byId = (id) => document.getElementById(id);
  const backendAnomalyApi = `${window.location.protocol}//${window.location.hostname}:8082/api/v1/anomalies`;
  let durableWaitingCount = null;

  const humanizeRole = (value) => {
    if (!value) return "—";
    return String(value)
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

  const renderQueue = (depth, maxSize, activeAnomaly) => {
    const normalizedDepth = Math.max(Number(depth || 0), 0);
    const depthNode = byId("workflow-queue-depth");
    const capacityNode = byId("workflow-queue-capacity");
    const queueMessage = byId("workflow-queue-message");

    if (depthNode) depthNode.textContent = String(normalizedDepth);
    if (capacityNode) capacityNode.textContent = `${normalizedDepth} / ${Number(maxSize || 4096)}`;
    if (queueMessage) {
      queueMessage.textContent = normalizedDepth
        ? `${normalizedDepth} ${normalizedDepth === 1 ? "anomaly is" : "anomalies are"} waiting for the current collaborative workflow to finish.`
        : activeAnomaly
          ? "The team owns one anomaly and no additional anomalies are waiting."
          : "No anomalies are currently queued.";
    }
  };

  const renderWorkflow = (workflow) => {
    if (!workflow) return;

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
    if (anomalyNode) anomalyNode.textContent = anomaly?.detector_name || anomaly?.detector_id || "—";
    if (resultNode) {
      resultNode.textContent = anomaly?.result_id || (
        state === "RECOVERY"
          ? "Persisted workflow from an earlier runtime"
          : "No anomaly currently owned by the team"
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

    const runtimeDepth = Number(workflow.queue_depth || 0);
    const depth = durableWaitingCount === null ? runtimeDepth : durableWaitingCount;
    const maxSize = Number(workflow.queue_maxsize || 4096);
    const maxConcurrent = Number(workflow.max_concurrent_anomalies || 1);
    const activeSlots = anomaly ? 1 : 0;
    const activeSlotsNode = byId("workflow-active-slots");
    if (activeSlotsNode) activeSlotsNode.textContent = `${activeSlots} / ${maxConcurrent}`;
    renderQueue(depth, maxSize, Boolean(anomaly));

    const error = byId("workflow-error");
    const errorMessage = byId("workflow-error-message");
    if (error) error.hidden = !workflow.last_error;
    if (errorMessage) errorMessage.textContent = workflow.last_error || "";
  };

  const syncDurableQueueCount = (summary) => {
    durableWaitingCount = Math.max(Number(summary?.waiting || 0), 0);
    const activeAnomaly = byId("workflow-anomaly")?.textContent?.trim();
    const capacityNode = byId("workflow-queue-capacity");
    let maxSize = 4096;
    if (capacityNode) {
      const parts = capacityNode.textContent.split("/");
      if (parts.length > 1) maxSize = Number(parts[1].trim()) || 4096;
    }
    renderQueue(
      durableWaitingCount,
      maxSize,
      Boolean(activeAnomaly && activeAnomaly !== "—"),
    );
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
    try {
      const response = await fetch("/api/overview", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      renderWorkflow(payload.workflow);
    } catch (_) {
      // Preserve the last server-rendered workflow snapshot on transient errors.
    }
  };

  const refreshAnomalyInbox = async () => {
    try {
      const response = await fetch(
        "/api/anomalies?state=WAITING&limit=4096&ascending=true",
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
  window.setInterval(refreshWorkflow, 10000);
  window.setInterval(refreshAnomalyInbox, 10000);
})();