(() => {
  const clock = document.getElementById("live-clock");
  const updateClock = () => {
    if (!clock) return;
    const now = new Date();
    clock.textContent = `${now.toISOString().slice(11, 19)} UTC`;
  };
  updateClock();
  window.setInterval(updateClock, 1000);

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

  const metricNodes = document.querySelectorAll("[data-metric]");
  const refreshOverview = async () => {
    if (!metricNodes.length) return;
    try {
      const response = await fetch("/api/overview", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      metricNodes.forEach((node) => {
        const key = node.dataset.metric;
        if (Object.prototype.hasOwnProperty.call(payload.overview, key)) {
          node.textContent = payload.overview[key];
        }
      });
    } catch (_) {
      // The static server-rendered state remains visible if polling fails.
    }
  };

  window.setInterval(refreshOverview, 15000);
})();
