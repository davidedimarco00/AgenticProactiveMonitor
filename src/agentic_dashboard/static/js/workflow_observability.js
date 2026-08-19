(() => {
  const panel = document.getElementById("workflow-panel");
  if (!panel) return;

  const byId = (id) => document.getElementById(id);

  const humanizeRole = (value) => {
    if (!value) return "—";
    return String(value)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
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

  const renderWorkflow = (workflow) => {
    if (!workflow) return;

    const state = String(workflow.state || "IDLE").toUpperCase();
    panel.dataset.workflowState = state;

    const stateNode = byId("workflow-state");
    if (stateNode) stateNode.textContent = state;

    const dot = panel.querySelector(".workflow-state-dot");
    if (dot) {
      dot.classList.remove("state-idle", "state-processing", "state-queued");
      dot.classList.add(`state-${state.toLowerCase()}`);
    }

    const phase = byId("workflow-phase");
    if (phase) phase.textContent = workflow.phase || "ANOMALY INTAKE";

    replaceIncidentNode(workflow.active_incident_id || null);

    const anomaly = workflow.active_anomaly || null;
    const anomalyNode = byId("workflow-anomaly");
    const resultNode = byId("workflow-result-id");
    if (anomalyNode) anomalyNode.textContent = anomaly?.detector_id || "—";
    if (resultNode) {
      resultNode.textContent = anomaly?.result_id || "No anomaly currently owned by the team";
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

    const depth = Number(workflow.queue_depth || 0);
    const maxSize = Number(workflow.queue_maxsize || 0);
    const depthNode = byId("workflow-queue-depth");
    const capacityNode = byId("workflow-queue-capacity");
    const queueMessage = byId("workflow-queue-message");
    if (depthNode) depthNode.textContent = String(depth);
    if (capacityNode) capacityNode.textContent = `${depth} / ${maxSize}`;
    if (queueMessage) {
      queueMessage.textContent = depth
        ? `${depth} ${depth === 1 ? "anomaly is" : "anomalies are"} waiting for the current collaborative workflow to finish.`
        : "No additional anomalies are waiting.";
    }

    const error = byId("workflow-error");
    const errorMessage = byId("workflow-error-message");
    if (error) error.hidden = !workflow.last_error;
    if (errorMessage) errorMessage.textContent = workflow.last_error || "";
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

  refreshWorkflow();
  window.setInterval(refreshWorkflow, 10000);
})();
