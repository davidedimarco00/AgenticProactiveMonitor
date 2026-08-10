(() => {
  const clock = document.getElementById("live-clock");

  const updateClock = () => {
    if (!clock) return;
    const now = new Date();
    clock.textContent = `${now.toISOString().slice(11, 19)} UTC`;
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
        row.hidden = query && !row.textContent.toLowerCase().includes(query);
      });
    });
  };

  const refreshTeamActivity = async () => {
    const stateNode = document.getElementById("team-activity-state");
    if (!stateNode) return;

    try {
      const response = await fetch("/api/overview", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const team = payload.team;
      if (!team) return;

      stateNode.textContent = team.state;
      stateNode.classList.toggle("working", Boolean(team.working));
      stateNode.classList.toggle("idle", !team.working);

      const incidentNode = document.getElementById("team-active-incidents");
      if (incidentNode) {
        const suffix = team.active_incidents === 1 ? "incident" : "incidents";
        incidentNode.textContent = `${team.active_incidents} ${suffix} under investigation`;
      }

      (team.members || []).forEach((member) => {
        const row = document.querySelector(`[data-agent-jid="${member.jid}"]`);
        const activity = row?.querySelector("[data-agent-activity]");
        if (!activity) return;
        activity.textContent = member.activity;
        activity.classList.toggle("working", member.activity === "WORKING");
        activity.classList.toggle("idle", member.activity !== "WORKING");
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

  updateClock();
  window.setInterval(updateClock, 1000);
  bindRows();
  bindIncidentSearch();
  window.setInterval(refreshTeamActivity, 15000);
  window.setInterval(refreshSystemHealth, 15000);
})();
