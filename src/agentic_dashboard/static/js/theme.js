(() => {
  const STORAGE_KEY = "apm-dashboard-theme";
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");
  const metaTheme = document.querySelector('meta[name="theme-color"]');

  const currentTheme = () => (root.dataset.theme === "dark" ? "dark" : "light");

  const syncButton = () => {
    if (!button) return;
    const dark = currentTheme() === "dark";
    const icon = button.querySelector(".theme-toggle-icon");
    const label = button.querySelector(".theme-toggle-label");
    if (icon) icon.textContent = dark ? "☀" : "☾";
    if (label) label.textContent = dark ? "Light" : "Night";
    button.setAttribute("aria-pressed", String(dark));
    button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    button.title = dark ? "Switch to light mode" : "Switch to dark mode";
    if (metaTheme) metaTheme.setAttribute("content", dark ? "#08111f" : "#f4f7fb");
  };

  const applyTheme = (theme, persist = true) => {
    const normalized = theme === "dark" ? "dark" : "light";
    root.dataset.theme = normalized;
    if (persist) {
      try { localStorage.setItem(STORAGE_KEY, normalized); } catch (_) {}
    }
    syncButton();
  };

  button?.addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  });

  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    if (event.newValue === "dark" || event.newValue === "light") applyTheme(event.newValue, false);
  });

  syncButton();
})();
