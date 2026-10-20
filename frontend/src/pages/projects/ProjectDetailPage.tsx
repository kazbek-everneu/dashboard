import { useState, useEffect, useMemo } from "react"
import { useParams, useNavigate, Link } from "react-router-dom"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Pencil, Trash2, Plus, Save } from "lucide-react"
import { toast } from "sonner"
import { projectsApi } from "@/api/projects"
import { tasksApi } from "@/api/tasks"
import { extractApiError } from "@/lib/helpers"
import { normalizeViewport } from "@/lib/viewportUtils"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { FormField } from "@/components/ui/FormField"
import { FormDialogFooter } from "@/components/ui/FormDialogFooter"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/DataTable"
import { taskColumns } from "@/components/tasks/taskColumns"
import ProjectSettingsPage from "./ProjectSettingsPage"
import SitemapTab from "./SitemapTab"
import type { ProjectConfig, SitemapURL, ViewportConfig } from "@/types"

const editSchema = z.object({
  name: z.string().min(1, "Name is required"),
  url: z.string().url("Enter a valid URL"),
})

type EditValues = z.infer<typeof editSchema>
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

  const updateMutation = useMutation({
    mutationFn: (values: EditValues) => projectsApi.update(id!, values),
    onSuccess: () => {
      toast.success("Project updated.")
      setEditOpen(false)
      void queryClient.invalidateQueries({ queryKey: ["project", id] })
      void queryClient.invalidateQueries({ queryKey: ["projects"] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

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

  const {
    register: editRegister,
    handleSubmit: handleEditSubmit,
    reset: resetEdit,
    formState: { errors: editErrors },
  } = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    values: projectQuery.data
      ? { name: projectQuery.data.name, url: projectQuery.data.url }
      : undefined,
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

  if (projectQuery.isLoading) return <div className="text-muted-foreground">Loading…</div>
  if (!project) return <div className="text-destructive">Project not found.</div>

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
    <div className="space-y-6">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>}
        description={project.url}
        actions={
          <div className="flex gap-2 shrink-0">
            <Link to={`/tasks/new?project_id=${id}`}>
              <Button size="sm">
                <Plus className="h-4 w-4 mr-1" />
                New Task
              </Button>
            </Link>
            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
              <Pencil className="h-4 w-4 mr-1" />
              Edit
            </Button>
            <Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}>
              <Trash2 className="h-4 w-4 mr-1" />
              Delete
            </Button>
          </div>
        }
      />

      {/* Tabs */}
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="mt-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">Recent Tasks</h2>
            <Link to={`/tasks?project_id=${id}`}>
              <Button variant="ghost" size="sm" className="text-primary text-xs">
                View all tasks →
              </Button>
            </Link>
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
                <Link to={`/tasks/new?project_id=${id}`}>
                  <Button size="sm">
                    <Plus className="h-4 w-4 mr-1" />
                    New Task
                  </Button>
                </Link>
              ),
            }}
          />
        </TabsContent>

        {/* Settings Tab */}
        <TabsContent value="settings" className="mt-4">
          <div className="space-y-4">
            <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <section className="glass-card flex min-h-[640px] flex-col overflow-hidden">
                <div className="border-b surface-divider px-5 py-4">
                  <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-foreground">Sitemap</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Fetch, review, and curate project URLs in one place.
                  </p>
                </div>
                <div className="flex-1 overflow-y-auto p-5">
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
                </div>
              </section>

              <section className="glass-card flex min-h-[640px] flex-col overflow-hidden">
                <div className="border-b surface-divider px-5 py-4">
                  <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-foreground">Configuration</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Adjust browsers, viewports, and capture behavior.
                  </p>
                </div>
                <div className="flex-1 overflow-y-auto p-5">
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
                </div>
              </section>
            </div>

            <div className="glass-card flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-muted-foreground">
                {hasUnsavedChanges ? "• Unsaved changes" : "All settings are saved"}
              </div>
              <Button
                onClick={() => void handleSaveSettings()}
                disabled={!hasUnsavedChanges || isSavingSettings || configInvalid}
              >
                <Save className="h-4 w-4 mr-2" />
                {isSavingSettings ? "Saving…" : "Save settings"}
              </Button>
            </div>
          </div>
        </TabsContent>
      </Tabs>

      {/* Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={(v: boolean) => { setEditOpen(v); if (!v) resetEdit() }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit project</DialogTitle>
            <DialogDescription>Update project name or base URL.</DialogDescription>
          </DialogHeader>
          <form onSubmit={handleEditSubmit((v) => updateMutation.mutate(v))} className="space-y-4 mt-2">
            <FormField label="Name" htmlFor="edit-name" error={editErrors.name?.message}>
              <Input id="edit-name" {...editRegister("name")} />
            </FormField>
            <FormField label="Base URL" htmlFor="edit-url" error={editErrors.url?.message}>
              <Input id="edit-url" {...editRegister("url")} />
            </FormField>
            <FormDialogFooter
              onCancel={() => setEditOpen(false)}
              isSubmitting={updateMutation.isPending}
              submitLabel="Save"
              submittingLabel="Saving…"
            />
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete project</DialogTitle>
            <DialogDescription>
              This will permanently delete &ldquo;{project.name}&rdquo; and all its tasks and
              screenshots. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
