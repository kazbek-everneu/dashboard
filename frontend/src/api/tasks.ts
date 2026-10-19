import apiClient from "./client"
import type { CreateTaskRequest, PaginatedResponse, Task, TaskStats } from "@/types"

export const tasksApi = {
  create: async (payload: CreateTaskRequest): Promise<Task> => {
    const { data } = await apiClient.post<Task>("/tasks/", payload)
    return data
  },

  get: async (id: string): Promise<Task> => {
    const { data } = await apiClient.get<Task>(`/tasks/${id}`)
    return data
  },

  list: async (params?: {
    projectId?: string
    limit?: number
    skip?: number
  }): Promise<PaginatedResponse<Task>> => {
    const { projectId, limit, skip } = params ?? {}
    const query: Record<string, string | number> = {}
    if (projectId) query.project_id = projectId
    if (limit) query.limit = limit
    if (skip) query.skip = skip
    const { data } = await apiClient.get<PaginatedResponse<Task>>("/tasks/", { params: query })
    return data
  },

  cancel: async (id: string): Promise<Task> => {
    const { data } = await apiClient.post<Task>(`/tasks/${id}/cancel`)
    return data
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/tasks/${id}`)
  },

  createArchive: async (taskId: string): Promise<{ archive_url: string; file_size: number }> => {
    const { data } = await apiClient.post<{ archive_url: string; file_size: number }>(
      `/tasks/${taskId}/archive`,
    )
    return data
  },

  stats: async (): Promise<TaskStats> => {
    const { data } = await apiClient.get<TaskStats>("/tasks/stats")
    return data
  },
}
