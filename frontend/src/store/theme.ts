import { create } from "zustand"

export type Theme = "light" | "dark" | "system"
type ResolvedTheme = "light" | "dark"

interface ThemeState {
  theme: Theme
  resolvedTheme: ResolvedTheme
  setTheme: (theme: Theme) => void
}

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)")

function applyTheme(theme: Theme): ResolvedTheme {
  const resolved: ResolvedTheme =
    theme === "system" ? (darkQuery.matches ? "dark" : "light") : theme
  const root = document.documentElement
  root.classList.toggle("dark", resolved === "dark")
  root.style.colorScheme = resolved
  localStorage.setItem("theme", theme)
  return resolved
}

// Read persisted preference; default to light
const stored = localStorage.getItem("theme")
const initialTheme: Theme = stored === "dark" || stored === "system" ? stored : "light"

// Belt-and-suspenders: ensure the class is in sync even if the index.html
// inline script was somehow bypassed (e.g. SSR, testing).
const initialResolved = applyTheme(initialTheme)

export const useThemeStore = create<ThemeState>((set) => ({
  theme: initialTheme,
  resolvedTheme: initialResolved,

  setTheme: (theme) => set({ theme, resolvedTheme: applyTheme(theme) }),
}))

// In "system" mode, follow OS theme changes live.
darkQuery.addEventListener("change", () => {
  const { theme, setTheme } = useThemeStore.getState()
  if (theme === "system") setTheme("system")
})
