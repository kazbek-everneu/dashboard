import { create } from "zustand"
import type { User } from "@/types"

interface AuthState {
  user: User | null
  initialized: boolean
  login: (user: User) => void
  logout: () => void
  setUser: (user: User) => void
  setInitialized: (initialized: boolean) => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  initialized: false,

  login: (user) => set({ user, initialized: true }),

  logout: () => {
    set({ user: null, initialized: true })
  },

  setUser: (user) => set({ user }),
  setInitialized: (initialized) => set({ initialized }),
}))
