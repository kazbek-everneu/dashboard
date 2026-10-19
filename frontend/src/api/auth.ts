import apiClient from "./client"
import type {
  PasswordResetConfirmRequest,
  PasswordResetRequest,
  PasswordResetResponse,
  RegisterRequest,
  TokenResponse,
  User,
} from "@/types"

export const authApi = {
  login: async (email: string, password: string): Promise<TokenResponse> => {
    // FastAPI OAuth2 expects form-encoded data
    const form = new URLSearchParams()
    form.set("username", email)
    form.set("password", password)
    const { data } = await apiClient.post<TokenResponse>("/auth/login", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    })
    return data
  },

  register: async (payload: RegisterRequest): Promise<User> => {
    const { data } = await apiClient.post<User>("/auth/register", payload)
    return data
  },

  requestPasswordReset: async (
    payload: PasswordResetRequest,
  ): Promise<PasswordResetResponse> => {
    const { data } = await apiClient.post<PasswordResetResponse>(
      "/auth/password-reset/request",
      payload,
    )
    return data
  },

  confirmPasswordReset: async (
    payload: PasswordResetConfirmRequest,
  ): Promise<PasswordResetResponse> => {
    const { data } = await apiClient.post<PasswordResetResponse>(
      "/auth/password-reset/confirm",
      payload,
    )
    return data
  },

  me: async (): Promise<User> => {
    const { data } = await apiClient.get<User>("/auth/me")
    return data
  },

  logout: async (): Promise<void> => {
    await apiClient.post("/auth/logout")
  },
}
