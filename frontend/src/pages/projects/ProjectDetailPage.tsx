import { useState, useEffect, useMemo } from "react"
import { useParams, useNavigate, Link } from "react-router-dom"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, Pencil, Trash2, Plus, Save } from "lucide-react"
import { toast } from "sonner"
import { projectsApi } from "@/api/projects"
import { tasksApi } from "@/api/tasks"
import { extractApiError } from "@/lib/helpers"
import { normalizeViewport } from "@/lib/viewportUtils"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { EditProjectDialog } from "@/components/projects/EditProjectDialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { StateMessage } from "@/components/ui/StateMessage"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { DataTable } from "@/components/ui/DataTable"
import { taskColumns } from "@/components/tasks/taskColumns"
import ProjectSettingsPage from "./ProjectSettingsPage"
import SitemapTab from "./SitemapTab"
import type { ProjectConfig, SitemapURL, ViewportConfig } from "@/types"

type PendingUrl = { tempId: string; url: string }

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [defaultBrowsers, setDefaultBrowsers] = useState<string[]>(["chrome"])
  const [viewports, setViewports] = useState<ViewportConfig[]>([])
  const [waitUntil, setWaitUntil] = useState<ProjectConfig["wait_until"]>("load")
  const [timeout, setTimeout] = useState("30000")
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [initialSelectedIds, setInitialSelectedIds] = useState<Set<string>>(new Set())
  const [pendingNewUrls, setPendingNewUrls] = useState<PendingUrl[]>([])
  const [deleteIds, setDeleteIds] = useState<Set<string>>(new Set())
  const [isSavingSettings, setIsSavingSettings] = useState(false)

  const projectQuery = useQuery({
    queryKey: ["project", id],
    queryFn: () => projectsApi.get(id!),
    enabled: !!id,
  })

  const tasksQuery = useQuery({
    queryKey: ["tasks", id, { limit: 10 }],
    queryFn: () => tasksApi.list({ projectId: id!, limit: 10 }),
    enabled: !!id,
  })

  const configQuery = useQuery({
    queryKey: ["project-config", id],
    queryFn: () => projectsApi.getConfig(id!),
    enabled: !!id,
  })

  const sitemapQuery = useQuery({
    queryKey: ["sitemap-urls", id],
    queryFn: () => projectsApi.getSitemapUrls(id!),
    enabled: !!id,
  })

  const hasActive = (tasksQuery.data?.items ?? []).some(
    (t) => t.status === "running" || t.status === "pending",
  )

  useEffect(() => {
    if (!hasActive) return
    const intervalId = setInterval(() => void tasksQuery.refetch(), 5000)
    return () => clearInterval(intervalId)
  }, [hasActive]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!configQuery.data) return
    setDefaultBrowsers(configQuery.data.default_browsers)
    setViewports(configQuery.data.viewports.map(normalizeViewport))
    setWaitUntil(configQuery.data.wait_until)
    setTimeout(String(configQuery.data.timeout))
  }, [configQuery.data])

  useEffect(() => {
    const apiUrls = sitemapQuery.data ?? []
    const ids = new Set(apiUrls.filter((u) => u.is_enabled).map((u) => u.id))
    setSelectedIds(new Set(ids))
    setInitialSelectedIds(new Set(ids))
    setPendingNewUrls([])
    setDeleteIds(new Set())
  }, [sitemapQuery.data])

  const fetchSitemapMutation = useMutation({
    mutationFn: () => projectsApi.fetchSitemap(id!),
    onSuccess: async () => {
      toast.success("Sitemap fetched.")
      await queryClient.invalidateQueries({ queryKey: ["sitemap-urls", id] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: () => projectsApi.delete(id!),
    onSuccess: () => {
      toast.success("Project deleted.")
      navigate("/projects", { replace: true })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const project = projectQuery.data
  const recentTasks = tasksQuery.data?.items ?? []
  const apiUrls = sitemapQuery.data ?? []
  const allUrls: SitemapURL[] = useMemo(
    () => [
      ...apiUrls.filter((u) => !deleteIds.has(u.id)),
      ...pendingNewUrls.map((p) => ({
        id: p.tempId,
        project_id: id ?? "",
        url: p.url,
        cpt: "manual",
        is_enabled: true,
        last_fetched_at: null,
      })),
    ],
    [apiUrls, deleteIds, pendingNewUrls, id],
  )
  const normalizedCurrentViewports = useMemo(
    () => viewports.map(normalizeViewport),
    [viewports],
  )
  const configDirty = useMemo(() => {
    if (!configQuery.data) return false
    return (
      configQuery.data.wait_until !== waitUntil ||
      String(configQuery.data.timeout) !== timeout ||
      JSON.stringify(configQuery.data.default_browsers) !== JSON.stringify(defaultBrowsers) ||
      JSON.stringify(configQuery.data.viewports.map(normalizeViewport)) !== JSON.stringify(normalizedCurrentViewports)
    )
  }, [configQuery.data, waitUntil, timeout, defaultBrowsers, normalizedCurrentViewports])
  const sitemapDirty = useMemo(() => {
    if (pendingNewUrls.length > 0 || deleteIds.size > 0) return true
    if (selectedIds.size !== initialSelectedIds.size) return true
    for (const selectedId of selectedIds) {
      if (!initialSelectedIds.has(selectedId)) return true
    }
    return false
  }, [pendingNewUrls, deleteIds, selectedIds, initialSelectedIds])
  const hasUnsavedChanges = configDirty || sitemapDirty
  const parsedTimeout = Number(timeout)
  const timeoutError =
    timeout.trim() === ""
      ? "Timeout is required"
      : !Number.isInteger(parsedTimeout)
        ? "Timeout must be a whole number"
        : parsedTimeout < 1000 || parsedTimeout > 120000
          ? "Timeout must be between 1000 and 120000"
          : undefined
  const configInvalid = !!timeoutError || viewports.length === 0

  useEffect(() => {
    if (!hasUnsavedChanges) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [hasUnsavedChanges])

  useBreadcrumbs([
    { label: "Projects", to: "/projects" },
    { label: project?.name ?? "Project" },
  ])

  if (projectQuery.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-72 w-full" />
      </div>
    )
  }
  if (!project) {
    return (
      <StateMessage
        compact
        tone="destructive"
        title="Project not found"
        description="It may have been deleted."
        action={
          <Button asChild variant="outline">
            <Link to="/projects">Back to projects</Link>
          </Button>
        }
      />
    )
  }

  const handleAddManualUrl = (url: string) => {
    const tempId = `pending_${Date.now()}_${Math.random().toString(36).slice(2)}`
    setPendingNewUrls((prev) => [...prev, { tempId, url }])
    setSelectedIds((prev) => new Set([...prev, tempId]))
  }

  const handleDeleteManualUrl = (urlId: string) => {
    if (urlId.startsWith("pending_")) {
      setPendingNewUrls((prev) => prev.filter((p) => p.tempId !== urlId))
      setSelectedIds((prev) => {
        const next = new Set(prev)
        next.delete(urlId)
        return next
      })
      return
    }

    setDeleteIds((prev) => new Set([...prev, urlId]))
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.delete(urlId)
      return next
    })
  }

  const handleSaveSettings = async () => {
    if (!id || isSavingSettings || !hasUnsavedChanges) return
    if (configInvalid) {
      toast.error(viewports.length === 0 ? "At least one viewport is required." : timeoutError ?? "Settings are invalid.")
      return
    }

    setIsSavingSettings(true)
    let sitemapSaved = false

    try {
      if (sitemapDirty) {
        const updates = apiUrls
          .filter((u) => !deleteIds.has(u.id))
          .map((u) => ({ url_id: u.id, is_enabled: selectedIds.has(u.id) }))
        const newUrls = pendingNewUrls.map((p) => ({ url: p.url, cpt: "manual" }))
        await projectsApi.bulkUpdateSitemapUrls(id, {
          updates,
          new_urls: newUrls,
          delete_ids: [...deleteIds],
        })
        sitemapSaved = true
        await queryClient.invalidateQueries({ queryKey: ["sitemap-urls", id] })
      }

      if (configDirty) {
        await projectsApi.updateConfig(id, {
          wait_until: waitUntil,
          timeout: parsedTimeout,
          default_browsers: defaultBrowsers,
          viewports: normalizedCurrentViewports,
        })
        await queryClient.invalidateQueries({ queryKey: ["project-config", id] })
      }

      toast.success("Settings saved.")
    } catch (err) {
      if (sitemapSaved) {
        await queryClient.invalidateQueries({ queryKey: ["sitemap-urls", id] })
      }
      toast.error(extractApiError(err))
    } finally {
      setIsSavingSettings(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>}
        description={project.url}
        actions={
          <>
            <Button asChild>
              <Link to={`/tasks/new?project_id=${id}`}>
                <Plus data-icon="inline-start" />
                New Task
              </Link>
            </Button>
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              <Pencil data-icon="inline-start" />
              Edit
            </Button>
            <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
              <Trash2 data-icon="inline-start" />
              Delete
            </Button>
          </>
        }
      />

      {/* Tabs */}
      <Tabs defaultValue="overview" className="gap-4">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-medium">Recent tasks</h2>
            <Button asChild variant="ghost" size="sm">
              <Link to={`/tasks?project_id=${id}`}>
                View all tasks
                <ArrowRight data-icon="inline-end" />
              </Link>
            </Button>
          </div>
          <DataTable
            columns={taskColumns}
            data={recentTasks}
            rowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/tasks/${row.id}`)}
            isLoading={tasksQuery.isLoading}
            emptyState={{
              title: "No tasks yet",
              description: "Create the first task to start screenshotting.",
              action: (
                <Button asChild>
                  <Link to={`/tasks/new?project_id=${id}`}>
                    <Plus data-icon="inline-start" />
                    New Task
                  </Link>
                </Button>
              ),
            }}
          />
        </TabsContent>

        {/* Settings Tab */}
        <TabsContent value="settings" className="flex flex-col gap-4">
          <div className="grid items-start gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader className="border-b">
                <CardTitle>Sitemap</CardTitle>
                <CardDescription>Fetch, review, and curate project URLs in one place.</CardDescription>
              </CardHeader>
              <CardContent>
                <SitemapTab
                  apiUrls={apiUrls}
                  urls={allUrls}
                  selectedIds={selectedIds}
                  onSelectionChange={setSelectedIds}
                  onAddManualUrl={handleAddManualUrl}
                  onDeleteManualUrl={handleDeleteManualUrl}
                  onFetchSitemap={() => fetchSitemapMutation.mutate()}
                  isLoading={sitemapQuery.isLoading}
                  isFetching={fetchSitemapMutation.isPending}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="border-b">
                <CardTitle>Configuration</CardTitle>
                <CardDescription>Adjust browsers, viewports, and capture behavior.</CardDescription>
              </CardHeader>
              <CardContent>
                <ProjectSettingsPage
                  isLoading={configQuery.isLoading}
                  defaultBrowsers={defaultBrowsers}
                  viewports={viewports}
                  waitUntil={waitUntil}
                  timeout={timeout}
                  timeoutError={timeoutError}
                  onDefaultBrowsersChange={setDefaultBrowsers}
                  onViewportsChange={setViewports}
                  onWaitUntilChange={setWaitUntil}
                  onTimeoutChange={setTimeout}
                />
              </CardContent>
            </Card>
          </div>

          {/* Sticks to the bottom of the viewport while the settings scroll */}
          <div className="sticky bottom-4 z-10 flex flex-col gap-3 rounded-xl border bg-card/95 px-4 py-3 shadow-sm backdrop-blur supports-backdrop-filter:bg-card/80 sm:flex-row sm:items-center sm:justify-between">
            {hasUnsavedChanges ? (
              <span className="flex items-center gap-2 text-sm font-medium">
                <span className="size-2 rounded-full bg-warning" />
                Unsaved changes
              </span>
            ) : (
              <span className="text-sm text-muted-foreground">All settings are saved</span>
            )}
            <Button
              onClick={() => void handleSaveSettings()}
              disabled={!hasUnsavedChanges || isSavingSettings || configInvalid}
            >
              {isSavingSettings ? <Spinner data-icon="inline-start" /> : <Save data-icon="inline-start" />}
              {isSavingSettings ? "Saving…" : "Save settings"}
            </Button>
          </div>
        </TabsContent>
      </Tabs>

      <EditProjectDialog project={editOpen ? project : null} onClose={() => setEditOpen(false)} />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete project"
        description={`This will permanently delete “${project.name}” and all its tasks and screenshots. This cannot be undone.`}
        confirmLabel="Delete"
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}
