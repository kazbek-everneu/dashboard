import apiClient from "./client"
import type { ApplicationSettings, ApplicationSettingsUpdate } from "@/types"

export const settingsApi = {
  get: async (): Promise<ApplicationSettings> => {
    const { data } = await apiClient.get<ApplicationSettings>("/settings/")
    return data
  },

  update: async (payload: ApplicationSettingsUpdate): Promise<ApplicationSettings> => {
    const { data } = await apiClient.patch<ApplicationSettings>("/settings/", payload)
    return data
  },
}
