import { useAuthStore } from "@/store/auth"

export function useAuth() {
  const { user, initialized, login, logout, setUser, setInitialized } = useAuthStore()
  return {
    user,
    initialized,
    login,
    logout,
    setUser,
    setInitialized,
    isAuthenticated: !!user,
  }
}
