// Light, dark, or follow the system. A per-viewer convenience kept in local storage; the page works without it.
const KEY = "nrfwb-theme";
const ORDER = ["system", "light", "dark"];

export function currentTheme() {
  try {
    const stored = localStorage.getItem(KEY);
    return ORDER.includes(stored) ? stored : "system";
  } catch (e) {
    return "system";
  }
}

export function applyTheme(theme) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function nextTheme(theme) {
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
  try {
    localStorage.setItem(KEY, next);
  } catch (e) {
    /* storage may be blocked: the theme still changes for this visit */
  }
  applyTheme(next);
  return next;
}
