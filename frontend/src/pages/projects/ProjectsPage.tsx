import { useMemo, useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Plus, FolderOpen, Camera, ListTodo, Pencil, Trash2, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { projectsApi } from "@/api/projects"
import { tasksApi } from "@/api/tasks"
import { extractApiError } from "@/lib/helpers"
import { formatRelativeTime, formatDate } from "@/lib/formatters"
import PageHeader from "@/components/layout/PageHeader"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { FormField } from "@/components/ui/FormField"
import { FormDialogFooter } from "@/components/ui/FormDialogFooter"
import { StatsCard } from "@/components/ui/StatsCard"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { DataTable, type ColumnDef } from "@/components/ui/DataTable"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import type { Project, ProjectSummary } from "@/types"

// ─── Types ────────────────────────────────────────────────────────────────────

interface EnrichedProject extends Project {
  last_task_status: string | null
  screenshots_count: number
}

// ─── Column definitions ───────────────────────────────────────────────────────

const projectColumns: ColumnDef<EnrichedProject>[] = [
  {
    key: "name",
    header: "Project",
    width: "w-[250px]",
    sortKey: "name",
    cell: (row) => (
      <div>
        <p className="font-medium text-foreground">{row.name}</p>
        <p className="text-xs text-muted-foreground font-mono mt-0.5">{row.id.slice(0, 8)}</p>
      </div>
    ),
  },
  {
    key: "url",
    header: "URL",
    cell: (row) => (
      <span className="font-mono text-xs text-muted-foreground truncate block max-w-[260px]" title={row.url}>
        {row.url}
      </span>
    ),
  },
  {
    key: "last_task",
    header: "Last Task",
    width: "w-[140px]",
    cell: (row) =>
      row.last_task_status ? (
        <StatusBadge status={row.last_task_status} />
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    key: "screenshots",
    header: "Screenshots",
    width: "w-[120px]",
    hideOnMobile: true,
    cell: (row) => (
      <span className="text-sm">{row.screenshots_count > 0 ? row.screenshots_count : "—"}</span>
    ),
  },
  {
    key: "created_at",
    header: "Created",
    width: "w-[120px]",
    sortKey: "created_at",
    hideOnMobile: true,
    cell: (row) => (
      <span className="text-sm text-muted-foreground" title={formatDate(row.created_at)}>
        {formatRelativeTime(row.created_at)}
      </span>
    ),
  },
]

// ─── Form schema ──────────────────────────────────────────────────────────────

const createProjectSchema = z.object({
  url: z.string().url("Enter a valid URL (e.g. https://example.com)"),
})
const editProjectSchema = z.object({
  name: z.string().min(1, "Name is required"),
  url: z.string().url("Enter a valid URL (e.g. https://example.com)"),
})
type CreateProjectFormValues = z.infer<typeof createProjectSchema>
type EditProjectFormValues = z.infer<typeof editProjectSchema>

interface InitialSitemapImportState {
  projectId: string
  projectName: string
  projectUrl: string
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ProjectsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<EnrichedProject | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<EnrichedProject | null>(null)
  const [initialSitemapImport, setInitialSitemapImport] = useState<InitialSitemapImportState | null>(null)
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({
    key: "created_at",
    direction: "desc",
  })

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
  })

  const { data: summaries = [] } = useQuery({
    queryKey: ["projects", "summary"],
    queryFn: projectsApi.summary,
  })

  const { data: taskStats } = useQuery({
    queryKey: ["tasks", "stats"],
    queryFn: tasksApi.stats,
  })

  // ─── Mutations ──────────────────────────────────────────────────────────────

  const fetchInitialSitemapMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => projectsApi.fetchSitemap(id),
    onSuccess: async (_, variables) => {
      toast.success("Sitemap imported.")
      setInitialSitemapImport((current) =>
        current?.projectId === variables.id ? null : current,
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
        queryClient.invalidateQueries({ queryKey: ["projects", "summary"] }),
        queryClient.invalidateQueries({ queryKey: ["sitemap-urls", variables.id] }),
      ])
    },
    onError: async (err, variables) => {
      setInitialSitemapImport((current) =>
        current?.projectId === variables.id ? null : current,
      )
      await queryClient.invalidateQueries({ queryKey: ["sitemap-urls", variables.id] })
      toast.error(extractApiError(err))
    },
  })

  const createMutation = useMutation({
    mutationFn: projectsApi.create,
    onSuccess: async (project) => {
      toast.success("Project created.")
      setCreateOpen(false)
      createForm.reset()
      setInitialSitemapImport({
        projectId: project.id,
        projectName: project.name,
        projectUrl: project.url,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
        queryClient.invalidateQueries({ queryKey: ["projects", "summary"] }),
      ])
      fetchInitialSitemapMutation.mutate({ id: project.id })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const editMutation = useMutation({
    mutationFn: (values: EditProjectFormValues) => projectsApi.update(editTarget!.id, values),
    onSuccess: () => {
      toast.success("Project updated.")
      setEditTarget(null)
      void queryClient.invalidateQueries({ queryKey: ["projects"] })
      void queryClient.invalidateQueries({ queryKey: ["projects", "summary"] })
      void queryClient.invalidateQueries({ queryKey: ["project", editTarget?.id] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: () => projectsApi.delete(deleteTarget!.id),
    onSuccess: () => {
      toast.success("Project deleted.")
      setDeleteTarget(null)
      void queryClient.invalidateQueries({ queryKey: ["projects"] })
      void queryClient.invalidateQueries({ queryKey: ["projects", "summary"] })
      void queryClient.invalidateQueries({ queryKey: ["tasks", "stats"] })
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  // ─── Forms ──────────────────────────────────────────────────────────────────

  const createForm = useForm<CreateProjectFormValues>({
    resolver: zodResolver(createProjectSchema),
  })

  const editForm = useForm<EditProjectFormValues>({
    resolver: zodResolver(editProjectSchema),
    values: editTarget ? { name: editTarget.name, url: editTarget.url } : undefined,
  })

  // ─── Derived data ───────────────────────────────────────────────────────────

  const rows = useMemo<EnrichedProject[]>(() => {
    const summaryMap = new Map<string, ProjectSummary>(
      summaries.map((summary) => [summary.project_id, summary]),
    )
    const enriched = projects.map((project): EnrichedProject => {
      const projectSummary = summaryMap.get(project.id)
      return {
        ...project,
        last_task_status: projectSummary?.last_task_status ?? null,
        screenshots_count: projectSummary?.screenshots_count ?? 0,
      }
    })

    return enriched.sort((a, b) => {
      let v = 0
      if (sort.key === "name") v = a.name.localeCompare(b.name)
      else if (sort.key === "created_at")
        v = new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      return sort.direction === "asc" ? v : -v
    })
  }, [projects, summaries, sort])

  const totalScreenshots = summaries.reduce((sum, s) => sum + s.screenshots_count, 0)

  return (
    <div className="page-shell">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Projects</h1>}
        description="Manage and monitor your web projects"
        meta={`${projects.length} active project${projects.length === 1 ? "" : "s"}`}
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            New Project
          </Button>
        }
      />

      {initialSitemapImport ? (
        <div className="glass-card flex items-center justify-between gap-4 px-4 py-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">
              Importing sitemap for {initialSitemapImport.projectName}
            </p>
            <p className="truncate text-xs text-muted-foreground" title={initialSitemapImport.projectUrl}>
              {initialSitemapImport.projectUrl}
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Fetching URLs…
          </div>
        </div>
      ) : null}

      {/* Stats row */}
      <div className="grid gap-4 md:grid-cols-3">
        <StatsCard icon={FolderOpen} value={projects.length} label="Total Projects" />
        <StatsCard icon={ListTodo} value={taskStats?.total ?? 0} label="Total Tasks" />
        <StatsCard icon={Camera} value={totalScreenshots} label="Screenshots Taken" />
      </div>

      {/* Table */}
      <DataTable
        columns={projectColumns}
        data={rows}
        rowKey={(row) => row.id}
        onRowClick={(row) => navigate(`/projects/${row.id}`)}
        rowActions={(row) => [
          {
            label: "Edit",
            icon: <Pencil size={14} />,
            onClick: () => setEditTarget(row),
          },
          {
            label: "Delete",
            icon: <Trash2 size={14} />,
            onClick: () => setDeleteTarget(row),
            variant: "destructive" as const,
          },
        ]}
        isLoading={isLoading}
        sort={sort}
        onSortChange={(key, direction) => setSort({ key, direction })}
        emptyState={{
          icon: <FolderOpen />,
          title: "No projects yet",
          description: "Create your first project to get started",
          action: (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              New Project
            </Button>
          ),
        }}
      />

      {/* Create dialog */}
      <Dialog open={createOpen} onOpenChange={(v) => { setCreateOpen(v); if (!v) createForm.reset() }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create project</DialogTitle>
            <DialogDescription>Add a website URL and import its sitemap automatically.</DialogDescription>
          </DialogHeader>
          <form onSubmit={createForm.handleSubmit((v) => createMutation.mutate(v))} className="space-y-4 mt-2">
            <FormField
              label="Base URL"
              htmlFor="create-url"
              error={createForm.formState.errors.url?.message}
            >
              <Input id="create-url" placeholder="https://example.com" {...createForm.register("url")} />
            </FormField>
            <FormDialogFooter
              onCancel={() => setCreateOpen(false)}
              isSubmitting={createMutation.isPending || fetchInitialSitemapMutation.isPending}
              submitLabel="Create"
              submittingLabel={createMutation.isPending ? "Creating…" : "Importing sitemap…"}
            />
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editTarget} onOpenChange={(v) => { if (!v) setEditTarget(null) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit project</DialogTitle>
            <DialogDescription>Update the project name or base URL.</DialogDescription>
          </DialogHeader>
          <form onSubmit={editForm.handleSubmit((v) => editMutation.mutate(v))} className="space-y-4 mt-2">
            <FormField
              label="Name"
              htmlFor="edit-name"
              error={editForm.formState.errors.name?.message}
            >
              <Input id="edit-name" {...editForm.register("name")} />
            </FormField>
            <FormField
              label="Base URL"
              htmlFor="edit-url"
              error={editForm.formState.errors.url?.message}
            >
              <Input id="edit-url" {...editForm.register("url")} />
            </FormField>
            <FormDialogFooter
              onCancel={() => setEditTarget(null)}
              isSubmitting={editMutation.isPending}
              submitLabel="Save"
              submittingLabel="Saving…"
            />
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        title="Delete project"
        description={`This will permanently delete “${deleteTarget?.name ?? "this project"}” and all its tasks and screenshots. This cannot be undone.`}
        confirmLabel="Delete"
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}
