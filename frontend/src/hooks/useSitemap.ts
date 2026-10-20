import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { projectsApi } from "@/api/projects"
import { toast } from "sonner"
import { extractApiError } from "@/lib/helpers"

export function useSitemap(projectId: string) {
  const queryClient = useQueryClient()

  const urlsQuery = useQuery({
    queryKey: ["sitemap-urls", projectId],
    queryFn: () => projectsApi.getSitemapUrls(projectId),
  })

  const fetchMutation = useMutation({
    mutationFn: () => projectsApi.fetchSitemap(projectId),
    onSuccess: () => {
      toast.success("Sitemap fetched successfully.")
      void queryClient.invalidateQueries({ queryKey: ["sitemap-urls", projectId] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ urlId, is_enabled }: { urlId: string; is_enabled: boolean }) =>
      projectsApi.toggleSitemapUrl(projectId, urlId, is_enabled),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sitemap-urls", projectId] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const addManualMutation = useMutation({
    mutationFn: (url: string) => projectsApi.addManualUrl(projectId, url),
    onSuccess: () => {
      toast.success("URL added.")
      void queryClient.invalidateQueries({ queryKey: ["sitemap-urls", projectId] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  return { urlsQuery, fetchMutation, toggleMutation, addManualMutation }
}
