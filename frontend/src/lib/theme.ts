export type Theme = "dark" | "light";

const KEY = "twa-theme";

export function getStoredTheme(): Theme {
  return (localStorage.getItem(KEY) as Theme) || "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("light", theme === "light");
  localStorage.setItem(KEY, theme);
}
