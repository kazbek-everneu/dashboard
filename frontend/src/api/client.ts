import axios from "axios"
import { toast } from "sonner"

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? "/api",
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
})

// Handle 401 and network errors globally
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Don't redirect on public auth pages.
      const currentPath = window.location.pathname
      const isPublicAuthPage = ["/login", "/forgot-password", "/reset-password"].some(
        (path) => currentPath.includes(path),
      )
      if (!isPublicAuthPage) {
        window.dispatchEvent(new Event("auth:logout"))
        window.location.href = "/login"
      }
    } else if (!error.response) {
      toast.error("Connection error — check your network or API server.")
    }
    return Promise.reject(error)
  },
)

export default apiClient
