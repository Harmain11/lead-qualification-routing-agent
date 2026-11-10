import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
export type ThemePreference = Theme | "system";

const STORAGE_KEY = "corral-theme";
const META_COLOR: Record<Theme, string> = { light: "#f5f7f4", dark: "#0c1116" };

function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function readThemePreference(): ThemePreference {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(preference: ThemePreference): Theme {
  return preference === "system" ? systemTheme() : preference;
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META_COLOR[theme]);
}

export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(readThemePreference);
  const [theme, setTheme] = useState<Theme>(() => resolveTheme(readThemePreference()));

  useEffect(() => {
    const next = resolveTheme(preference);
    setTheme(next);
    applyTheme(next);
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      const followed = media.matches ? "dark" : "light";
      setTheme(followed);
      applyTheme(followed);
    };
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      if (next === "system") window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* The choice still applies for this session. */
    }
  }, []);

  const toggle = useCallback(() => {
    setPreference(theme === "light" ? "dark" : "light");
  }, [setPreference, theme]);

  return { theme, preference, setPreference, toggle };
}
