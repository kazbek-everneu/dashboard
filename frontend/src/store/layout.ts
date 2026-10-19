import { create } from "zustand"

interface LayoutState {
  collapsed: boolean
  toggleCollapsed: () => void
}

// Read persisted preference; default to expanded
const initialCollapsed = localStorage.getItem("sidebar-collapsed") === "true"

export const useLayoutStore = create<LayoutState>((set) => ({
  collapsed: initialCollapsed,

  toggleCollapsed: () =>
    set((state) => {
      const next = !state.collapsed
      localStorage.setItem("sidebar-collapsed", String(next))
      return { collapsed: next }
    }),
}))
