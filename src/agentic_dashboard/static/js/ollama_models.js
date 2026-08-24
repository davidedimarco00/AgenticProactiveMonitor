(() => {
  const strip = document.getElementById("service-strip");
  if (!strip) return;

  const ollamaState = strip.querySelector('[data-service-name="Ollama"]');
  if (!ollamaState) return;

  const host = document.createElement("div");
  host.className = "ollama-runtime-models";
  host.id = "ollama-runtime-models";
  host.setAttribute("aria-live", "polite");
  host.innerHTML = `
    <span class="ollama-runtime-label">Loaded</span>
    <span class="ollama-runtime-empty">Checking…</span>
  `;

  const wrapper = document.createElement("div");
  wrapper.className = "ollama-service-stack";
  ollamaState.replaceWith(wrapper);
  wrapper.append(ollamaState, host);

  let requestInFlight = false;

  const formatBytes = (value) => {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = bytes;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    const decimals = unit >= 3 ? 1 : 0;
    return `${amount.toFixed(decimals)} ${units[unit]}`;
  };

  const render = (payload) => {
    host.replaceChildren();
    const label = document.createElement("span");
    label.className = "ollama-runtime-label";
    label.textContent = "Loaded";
    host.appendChild(label);

    const models = Array.isArray(payload?.models) ? payload.models : [];
    if (!models.length) {
      const empty = document.createElement("span");
      empty.className = "ollama-runtime-empty";
      empty.textContent = payload?.status === "offline"
        ? "Unavailable"
        : "No model in memory";
      host.appendChild(empty);
      return;
    }

    models.forEach((model) => {
      const chip = document.createElement("span");
      chip.className = "ollama-model-chip";
      const name = String(model?.name || model?.model || "model");
      chip.textContent = name;

      const detail = model?.size_vram ? ` · VRAM ${formatBytes(model.size_vram)}` : "";
      const expires = model?.expires_at ? ` · loaded until ${model.expires_at}` : "";
      chip.title = `${name}${detail}${expires}`;
      host.appendChild(chip);
    });
  };

  const refresh = async () => {
    if (requestInFlight || document.hidden) return;
    requestInFlight = true;
    try {
      const response = await fetch("/api/ollama-loaded-models", { cache: "no-store" });
      if (!response.ok) {
        render({ status: "offline", models: [] });
        return;
      }
      render(await response.json());
    } catch (_) {
      render({ status: "offline", models: [] });
    } finally {
      requestInFlight = false;
    }
  };

  refresh();
  window.setInterval(refresh, 3000);
})();
