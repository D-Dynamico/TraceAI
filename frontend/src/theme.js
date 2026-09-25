// Light/dark choice. The default is the OS setting; the nav toggle records an
// explicit choice, which then wins in both directions. The CSS side keys off
// `data-theme` on <html> (see src/index.css and themeCss() in categories.js):
// absent → follow the OS, "light" / "dark" → forced.
//
// index.html stamps the saved choice before first paint with an inline copy of
// readSaved(); keep the storage key in step with it.

export const THEME_KEY = "traceai-theme";

// Storage can be refused (private mode, blocked site data), so every access is
// guarded; a failure just means "follow the OS" and no persistence.
export function readSaved() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

export function systemPrefersDark() {
  return typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}

/** The theme actually showing: the saved choice, else the OS setting. */
export function effectiveTheme() {
  return readSaved() ?? (systemPrefersDark() ? "dark" : "light");
}

/** Force a theme, remember it, and stamp <html> so the CSS follows. */
export function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Not persisted; it still applies for this page view.
  }
}
