import apiClient from "./client"
import type { CreateUserRequest, PaginatedResponse, UpdateUserRequest, User } from "@/types"

export const usersApi = {
  list: async (params?: {
    limit?: number
    skip?: number
  }): Promise<PaginatedResponse<User>> => {
    const { limit, skip } = params ?? {}
    const query: Record<string, number> = {}
    if (limit) query.limit = limit
    if (skip) query.skip = skip
    const { data } = await apiClient.get<PaginatedResponse<User>>("/users/", { params: query })
    return data
  },

  create: async (payload: CreateUserRequest): Promise<User> => {
    const { data } = await apiClient.post<User>("/users/", payload)
    return data
  },

  update: async (id: string, payload: UpdateUserRequest): Promise<User> => {
    const { data } = await apiClient.patch<User>(`/users/${id}`, payload)
    return data
  },

  delete: async (id: string, reassignToUserId: string): Promise<void> => {
    await apiClient.delete(`/users/${id}`, {
      params: { reassign_to_user_id: reassignToUserId },
    })
  },
}
