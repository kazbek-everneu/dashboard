import apiClient from "./client"
import type { Project, ProjectConfig, ProjectConfigUpdate, ProjectSummary, SitemapURL } from "@/types"

export const projectsApi = {
  list: async (): Promise<Project[]> => {
    const { data } = await apiClient.get<Project[]>("/projects/")
    return data
  },

  get: async (id: string): Promise<Project> => {
    const { data } = await apiClient.get<Project>(`/projects/${id}`)
    return data
  },

  create: async (payload: { url: string }): Promise<Project> => {
    const { data } = await apiClient.post<Project>("/projects/", payload)
    return data
  },

  summary: async (): Promise<ProjectSummary[]> => {
    const { data } = await apiClient.get<ProjectSummary[]>("/projects/summary")
    return data
  },

  update: async (id: string, payload: Partial<{ name: string; url: string }>): Promise<Project> => {
    const { data } = await apiClient.patch<Project>(`/projects/${id}`, payload)
    return data
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/projects/${id}`)
  },

  // Config
  getConfig: async (id: string): Promise<ProjectConfig> => {
    const { data } = await apiClient.get<ProjectConfig>(`/projects/${id}/config`)
    return data
  },

  updateConfig: async (id: string, payload: ProjectConfigUpdate): Promise<ProjectConfig> => {
    const { data } = await apiClient.patch<ProjectConfig>(`/projects/${id}/config`, payload)
    return data
  },

  // Sitemap
  fetchSitemap: async (id: string): Promise<void> => {
    await apiClient.post(`/projects/${id}/sitemap/fetch`)
  },

  getSitemapUrls: async (id: string): Promise<SitemapURL[]> => {
    const { data } = await apiClient.get<SitemapURL[]>(`/projects/${id}/sitemap/urls`)
    return data
  },

  toggleSitemapUrl: async (
    projectId: string,
    urlId: string,
    is_enabled: boolean,
  ): Promise<SitemapURL> => {
    const { data } = await apiClient.patch<SitemapURL>(
      `/projects/${projectId}/sitemap/urls/${urlId}`,
      { is_enabled },
    )
    return data
  },

  addManualUrl: async (projectId: string, url: string): Promise<SitemapURL> => {
    const { data } = await apiClient.post<SitemapURL>(`/projects/${projectId}/sitemap/urls`, {
      url,
      cpt: "manual",
    })
    return data
  },

  bulkUpdateSitemapUrls: async (
    projectId: string,
    payload: {
      updates: { url_id: string; is_enabled: boolean }[]
      new_urls: { url: string; cpt: string }[]
      delete_ids: string[]
    },
  ): Promise<{ updated: number; created: number; deleted: number }> => {
    const { data } = await apiClient.patch(
      `/projects/${projectId}/sitemap/urls/bulk`,
      payload,
    )
    return data
  },
}
