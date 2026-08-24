(() => {
  const strip = document.getElementById("service-strip");
  if (!strip) return;

  const ollamaState = strip.querySelector('[data-service-name="Ollama"]');
  if (!ollamaState) return;

  const host = document.createElement("div");
  host.className = "ollama-model-monitor";
  host.id = "ollama-runtime-models";
  host.setAttribute("aria-live", "polite");
  host.innerHTML = `
    <div class="ollama-model-row">
      <span class="ollama-runtime-label">Configured</span>
      <span class="ollama-runtime-empty">Checking…</span>
    </div>
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

  const createText = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text;
    return node;
  };

  const createConfiguredChip = (model) => {
    const chip = document.createElement("span");
    const loaded = Boolean(model?.loaded);
    const available = model?.available;
    chip.className = `ollama-configured-chip ${loaded ? "loaded" : available === false ? "missing" : "standby"}`;

    const role = createText("span", "ollama-model-role", model?.role || "Model");
    const name = createText("strong", "ollama-model-name", model?.name || "unknown");
    const state = createText(
      "span",
      "ollama-model-state",
      loaded ? "LOADED" : available === false ? "NOT INSTALLED" : "STANDBY",
    );

    chip.append(role, name, state);
    chip.title = loaded
      ? `${model?.role || "Model"}: ${model?.name || "unknown"} · currently loaded in Ollama memory`
      : available === false
        ? `${model?.role || "Model"}: ${model?.name || "unknown"} · configured but not installed in Ollama`
        : `${model?.role || "Model"}: ${model?.name || "unknown"} · configured, not currently loaded`;
    return chip;
  };

  const createRuntimeChip = (model) => {
    const chip = document.createElement("span");
    chip.className = "ollama-model-chip";
    const name = String(model?.name || model?.model || "model");
    chip.textContent = name;

    const detail = model?.size_vram ? ` · VRAM ${formatBytes(model.size_vram)}` : "";
    const expires = model?.expires_at ? ` · loaded until ${model.expires_at}` : "";
    chip.title = `${name}${detail}${expires}`;
    return chip;
  };

  const appendRow = (labelText, className = "") => {
    const row = document.createElement("div");
    row.className = `ollama-model-row ${className}`.trim();
    row.appendChild(createText("span", "ollama-runtime-label", labelText));
    host.appendChild(row);
    return row;
  };

  const render = (payload) => {
    host.replaceChildren();

    const configuredRow = appendRow("Configured", "configured");
    const configured = Array.isArray(payload?.configured_models) ? payload.configured_models : [];
    if (!configured.length) {
      configuredRow.appendChild(createText("span", "ollama-runtime-empty", "Configuration unavailable"));
    } else {
      configured.forEach((model) => configuredRow.appendChild(createConfiguredChip(model)));
    }

    const loadedRow = appendRow("Loaded now", "runtime");
    const loaded = Array.isArray(payload?.models) ? payload.models : [];
    if (!loaded.length) {
      loadedRow.appendChild(createText(
        "span",
        "ollama-runtime-empty",
        payload?.status === "offline" ? "Ollama unavailable" : "No model in memory",
      ));
    } else {
      loaded.forEach((model) => loadedRow.appendChild(createRuntimeChip(model)));
    }
  };

  const refresh = async () => {
    if (requestInFlight || document.hidden) return;
    requestInFlight = true;
    try {
      const response = await fetch("/api/ollama-loaded-models", { cache: "no-store" });
      const payload = await response.json().catch(() => ({ status: "offline", models: [] }));
      render(payload);
    } catch (_) {
      render({ status: "offline", models: [], configured_models: [] });
    } finally {
      requestInFlight = false;
    }
  };

  refresh();
  window.setInterval(refresh, 3000);
})();
