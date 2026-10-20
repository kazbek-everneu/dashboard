import { create } from "zustand"

export interface Crumb {
  label: string
  to?: string
}

interface BreadcrumbState {
  // The path the crumbs were set for; the header ignores them elsewhere.
  pathname: string | null
  crumbs: Crumb[]
  setCrumbs: (pathname: string, crumbs: Crumb[]) => void
}

export const useBreadcrumbStore = create<BreadcrumbState>((set) => ({
  pathname: null,
  crumbs: [],
  setCrumbs: (pathname, crumbs) => set({ pathname, crumbs }),
}))
