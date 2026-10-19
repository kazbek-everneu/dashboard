import apiClient from "./client"
import type { Screenshot } from "@/types"

export const screenshotsApi = {
  list: async (taskId: string): Promise<Screenshot[]> => {
    const { data } = await apiClient.get<Screenshot[]>(`/screenshots/${taskId}`, {
      params: { limit: 1000 },
    })
    return data
  },

  recapture: async (screenshotId: string): Promise<Screenshot> => {
    const { data } = await apiClient.post<Screenshot>(
      `/screenshots/${screenshotId}/recapture`,
    )
    return data
  },
}
