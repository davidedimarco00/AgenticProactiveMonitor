(() => {
  const TARGET_SELECTOR =
    ".agent-detail-disclosure pre, .audit-json-disclosure pre";

  const looksLikeJson = (value) => {
    const text = String(value || "").trim();
    if (!text) return false;
    return (
      (text.startsWith("{") && text.endsWith("}")) ||
      (text.startsWith("[") && text.endsWith("]")) ||
      (text.startsWith('"') && text.endsWith('"'))
    );
  };

  const decodeEscapedText = (value) => {
    if (typeof value !== "string") return value;
    return value
      .replace(/\\r\\n/g, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\\t/g, "  ")
      .replace(/\\\"/g, '"');
  };

  const normalizeStructuredValue = (value, depth = 0) => {
    if (depth > 8) return value;

    if (Array.isArray(value)) {
      return value.map((item) => normalizeStructuredValue(item, depth + 1));
    }

    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [
          key,
          normalizeStructuredValue(item, depth + 1),
        ]),
      );
    }

    if (typeof value !== "string") return value;

    let text = decodeEscapedText(value);
    for (let attempt = 0; attempt < 4; attempt += 1) {
      if (!looksLikeJson(text)) break;
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed === "string" && parsed === text) break;
        return normalizeStructuredValue(parsed, depth + 1);
      } catch (_) {
        break;
      }
    }
    return text;
  };

  const indentMultiline = (value, indentation) => {
    const text = String(value ?? "");
    const lines = text.split("\n");
    if (lines.length <= 1) return text;
    return lines
      .map((line, index) => (index === 0 ? line : `${indentation}${line}`))
      .join("\n");
  };

  const formatReadable = (value, level = 0) => {
    const indent = "  ".repeat(level);
    const childIndent = "  ".repeat(level + 1);

    if (Array.isArray(value)) {
      if (!value.length) return "[]";
      const rows = value.map(
        (item) => `${childIndent}${formatReadable(item, level + 1)}`,
      );
      return `[\n${rows.join(",\n")}\n${indent}]`;
    }

    if (value && typeof value === "object") {
      const entries = Object.entries(value);
      if (!entries.length) return "{}";
      const rows = entries.map(([key, item]) => {
        const formatted = formatReadable(item, level + 1);
        return `${childIndent}${key}: ${formatted}`;
      });
      return `{\n${rows.join(",\n")}\n${indent}}`;
    }

    if (value === null) return "null";
    if (value === undefined) return "—";
    if (typeof value === "boolean" || typeof value === "number")
      return String(value);

    const cleaned = decodeEscapedText(String(value));
    return indentMultiline(cleaned, childIndent);
  };

  const parseRoot = (text) => {
    const raw = String(text || "").trim();
    if (!raw) return "";
    try {
      return normalizeStructuredValue(JSON.parse(raw));
    } catch (_) {
      return normalizeStructuredValue(raw);
    }
  };

  const prettyPrint = (pre) => {
    if (!(pre instanceof HTMLElement)) return;
    if (pre.dataset.prettyStructured === "true") return;

    const normalized = parseRoot(pre.textContent || "");
    pre.textContent = formatReadable(normalized);
    pre.dataset.prettyStructured = "true";
    pre.setAttribute("aria-label", "Readable structured JSON");
  };

  // Shared with the other observability views: an MCP outcome is an envelope
  // whose payload is itself a JSON string, so it must be unwrapped before it can
  // be shown to an operator instead of printing its escape sequences.
  window.apmStructured = {
    looksLikeJson,
    normalize: normalizeStructuredValue,
    toReadableText: (value) => formatReadable(normalizeStructuredValue(value)),
    isStructured: (value) => {
      if (value && typeof value === "object") return true;
      return typeof value === "string" && looksLikeJson(value);
    },
  };

  const processNode = (node) => {
    if (!(node instanceof Element)) return;
    if (node.matches(TARGET_SELECTOR)) prettyPrint(node);
    node.querySelectorAll?.(TARGET_SELECTOR).forEach(prettyPrint);
  };

  document.querySelectorAll(TARGET_SELECTOR).forEach(prettyPrint);

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach(processNode);
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });
})();
