import { create } from "zustand"

interface LayoutState {
  collapsed: boolean
  setCollapsed: (collapsed: boolean) => void
}

// Read persisted preference; default to expanded
const initialCollapsed = localStorage.getItem("sidebar-collapsed") === "true"

export const useLayoutStore = create<LayoutState>((set) => ({
  collapsed: initialCollapsed,

  setCollapsed: (collapsed) => {
    localStorage.setItem("sidebar-collapsed", String(collapsed))
    set({ collapsed })
  },
}))
