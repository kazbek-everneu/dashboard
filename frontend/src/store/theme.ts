import { create } from "zustand"

type Theme = "light" | "dark"

interface ThemeState {
  theme: Theme
  toggleTheme: () => void
}

function applyTheme(theme: Theme): void {
  if (theme === "dark") {
    document.documentElement.classList.add("dark")
  } else {
    document.documentElement.classList.remove("dark")
  }
  localStorage.setItem("theme", theme)
}

// Read persisted preference; default to light
const initialTheme = (localStorage.getItem("theme") as Theme | null) ?? "light"

// Belt-and-suspenders: ensure the class is in sync even if the index.html
// inline script was somehow bypassed (e.g. SSR, testing).
applyTheme(initialTheme)

export const useThemeStore = create<ThemeState>((set) => ({
  theme: initialTheme,

  toggleTheme: () =>
    set((state) => {
      const next: Theme = state.theme === "light" ? "dark" : "light"
      applyTheme(next)
      return { theme: next }
    }),
}))
