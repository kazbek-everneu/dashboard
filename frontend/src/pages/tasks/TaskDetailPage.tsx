import { useEffect, useMemo, useState } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Square, Trash2, Download, Archive, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { useTaskPolling } from "@/hooks/useTaskPolling"
import { tasksApi } from "@/api/tasks"
import { screenshotsApi } from "@/api/screenshots"
import { projectsApi } from "@/api/projects"
import { cn, formatDate, shortId, extractApiError } from "@/lib/utils"
import { formatFileSize } from "@/lib/formatters"
import { formatElapsedTime, formatThroughput, formatAverageSeconds, browserLabel } from "@/lib/task-formatters"
import PageHeader from "@/components/layout/PageHeader"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { TaskProgressBar } from "@/components/tasks/TaskProgressBar"
import { TaskStatsBar } from "@/components/tasks/TaskStatsBar"
import { LogsTable } from "@/components/tasks/LogsTable"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { StateMessage } from "@/components/ui/StateMessage"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { StatusBadge } from "@/components/ui/StatusBadge"
import ScreenshotsPage from "@/pages/screenshots/ScreenshotsPage"
import type { ViewportConfig } from "@/types"

const ACTIVE_STATUSES = new Set(["pending", "running"])
const DELETABLE_STATUSES = new Set(["completed", "failed", "cancelled", "timed_out"])
const FINAL_STATUSES = new Set(["completed", "failed", "cancelled", "timed_out"])
const API_BASE = import.meta.env.VITE_API_URL ?? "/api"
const PROGRESS_EXIT_MS = 240
const BROWSER_ORDER = ["chromium", "chrome", "firefox", "webkit", "msedge"]

type ArchiveState = "idle" | "loading" | "ready"

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [archiveState, setArchiveState] = useState<ArchiveState>("idle")
  const [archiveUrl, setArchiveUrl] = useState<string | null>(null)
  const [archiveSize, setArchiveSize] = useState<number>(0)
  const [showProgressCard, setShowProgressCard] = useState(false)
  const [isProgressExiting, setIsProgressExiting] = useState(false)

  const { data: task, isLoading } = useTaskPolling(id!)
  const isRunning = task?.status === "running"

  const { data: screenshots = [] } = useQuery({
    queryKey: ["screenshots", id],
    queryFn: () => screenshotsApi.list(id!),
    enabled: !!id,
    // Keep polling while the task runs OR any screenshot is being re-captured.
    refetchInterval: (query) =>
      isRunning || query.state.data?.some((s) => s.status === "recapturing") ? 3000 : false,
  })

  const { data: project } = useQuery({
    queryKey: ["project", task?.project_id],
    queryFn: () => projectsApi.get(task!.project_id),
    enabled: !!task?.project_id,
  })

  const { data: projectConfig } = useQuery({
    queryKey: ["project-config", task?.project_id],
    queryFn: () => projectsApi.getConfig(task!.project_id),
    enabled: !!task?.project_id,
  })

  const cancelMutation = useMutation({
    mutationFn: () => tasksApi.cancel(id!),
    onSuccess: (updated) => {
      toast.success("Task cancelled.")
      queryClient.setQueryData(["task", id], updated)
      void queryClient.invalidateQueries({ queryKey: ["tasks"] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: () => tasksApi.delete(id!),
    onSuccess: () => {
      toast.success("Task deleted.")
      void queryClient.invalidateQueries({ queryKey: ["tasks"] })
      navigate(task ? `/projects/${task.project_id}` : "/tasks")
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  async function handleGenerateArchive() {
    if (!id) return
    setArchiveState("loading")
    try {
      const { archive_url, file_size } = await tasksApi.createArchive(id)
      setArchiveUrl(archive_url)
      setArchiveSize(file_size)
      setArchiveState("ready")
    } catch (err: unknown) {
      setArchiveState("idle")
      toast.error(extractApiError(err))
    }
  }

  useEffect(() => {
    if (!task) return

    if (task.status === "running") {
      setShowProgressCard(true)
      setIsProgressExiting(false)
      return
    }

    if (!showProgressCard) return

    setIsProgressExiting(true)
    const timeoutId = window.setTimeout(() => {
      setShowProgressCard(false)
      setIsProgressExiting(false)
    }, PROGRESS_EXIT_MS)

    return () => window.clearTimeout(timeoutId)
  }, [task?.status, showProgressCard])

  const elapsedMs = useMemo(() => {
    if (!task?.started_at) return null
    const startedAt = new Date(task.started_at).getTime()
    const endAt = task.completed_at ? new Date(task.completed_at).getTime() : Date.now()
    const diff = endAt - startedAt
    return diff > 0 ? diff : null
  }, [task?.started_at, task?.completed_at])

  const participatingBrowsers = useMemo(() => {
    if (!task) return []
    const effectiveBaseBrowsers =
      task.override_browsers ?? (task.override_browser ? [task.override_browser] : projectConfig?.default_browsers ?? [])
    const effectiveViewports: ViewportConfig[] = task.override_viewports ?? projectConfig?.viewports ?? []
    const discovered = new Set<string>()
    if (effectiveViewports.length > 0) {
      for (const viewport of effectiveViewports) {
        const browsers = viewport.browsers ?? effectiveBaseBrowsers
        for (const browser of browsers) discovered.add(browser)
      }
    }
    if (discovered.size === 0) {
      for (const browser of effectiveBaseBrowsers) discovered.add(browser)
    }
    for (const screenshot of screenshots) discovered.add(screenshot.browser)
    return Array.from(discovered).sort((a, b) => {
      const aIndex = BROWSER_ORDER.indexOf(a)
      const bIndex = BROWSER_ORDER.indexOf(b)
      if (aIndex === -1 && bIndex === -1) return a.localeCompare(b)
      if (aIndex === -1) return 1
      if (bIndex === -1) return -1
      return aIndex - bIndex
    })
  }, [task, projectConfig, screenshots])

  const averageCaptureSeconds = useMemo(
    () =>
      screenshots.length > 0
        ? screenshots.reduce((sum, s) => sum + s.duration_seconds, 0) / screenshots.length
        : null,
    [screenshots],
  )

  if (isLoading) return <StateMessage compact title="Loading task" description="Preparing task details..." />
  if (!task) return <StateMessage compact tone="destructive" title="Task not found" />

  const okCount = task.progress_done
  const failedCount = task.progress_failed
  const processedCount = okCount + failedCount
  const showStatsRow = FINAL_STATUSES.has(task.status) && !showProgressCard && !isProgressExiting

  const uniqueUrlCount = new Set(task.target_urls).size
  const effectiveViewports: ViewportConfig[] = task.override_viewports ?? projectConfig?.viewports ?? []
  const effectiveBaseBrowsers =
    task.override_browsers ?? (task.override_browser ? [task.override_browser] : projectConfig?.default_browsers ?? [])
  const viewportCombinationCount = effectiveViewports.reduce((sum, viewport) => {
    const browsers = viewport.browsers ?? effectiveBaseBrowsers
    return sum + Math.max(browsers.length, 1)
  }, 0)

  const statsItems = [
    { label: "Shots", value: task.progress_total.toLocaleString() },
    { label: "Done", value: okCount.toLocaleString(), valueClassName: "text-sm font-semibold text-emerald-600 dark:text-emerald-400" },
    { label: "Errors", value: failedCount.toLocaleString(), valueClassName: failedCount > 0 ? "text-sm font-semibold text-destructive" : "text-sm font-semibold text-foreground" },
    { label: "Duration", value: formatElapsedTime(elapsedMs) },
    { label: "Speed", value: formatThroughput(processedCount, elapsedMs) },
    { label: "Avg time", value: formatAverageSeconds(averageCaptureSeconds) },
    { label: "Pages", value: uniqueUrlCount.toLocaleString() },
    {
      label: "Browsers",
      value:
        participatingBrowsers.length > 0 ? (
          <div className="flex items-center justify-center gap-1.5">
            {participatingBrowsers.map((browser) => (
              <span key={browser} className="inline-flex h-4 items-center" title={browserLabel(browser)}>
                <BrowserIcon browser={browser} size={16} className="block" />
              </span>
            ))}
          </div>
        ) : (
          "—"
        ),
    },
    { label: "Viewports", value: viewportCombinationCount > 0 ? viewportCombinationCount.toLocaleString() : "—" },
  ]

  return (
    <div className="page-shell">
      <PageHeader
        title={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate(`/projects/${task.project_id}`)}
              className="p-1 h-auto"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <h1 className="text-2xl font-bold font-mono">Task #{shortId(task.id)}</h1>
            <StatusBadge status={task.status} />
          </div>
        }
        description={
          <>
            {project ? `${project.name} · ` : ""}
            {formatDate(task.created_at)}
          </>
        }
        descriptionClassName="ml-8"
        contentClassName="space-y-1"
        footerClassName="pt-1"
        footer={showStatsRow ? (
          <TaskStatsBar items={statsItems} />
        ) : null}
        actions={
          <div className="flex items-center gap-2 shrink-0">
            {ACTIVE_STATUSES.has(task.status) && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
                className="text-destructive border-destructive/40 hover:bg-destructive/10"
              >
                <Square className="h-3.5 w-3.5 mr-1.5" />
                {cancelMutation.isPending ? "Stopping..." : "Stop"}
              </Button>
            )}

            {DELETABLE_STATUSES.has(task.status) && (
              archiveState === "ready" && archiveUrl ? (
                <Button variant="outline" size="sm" asChild>
                  <a
                    href={`${API_BASE}${archiveUrl}`}
                    download={`task-${id}.zip`}
                  >
                    <Download className="h-3.5 w-3.5 mr-1.5" />
                    Download ZIP
                    <span className="ml-1.5 text-xs text-muted-foreground">
                      {formatFileSize(archiveSize)}
                    </span>
                  </a>
                </Button>
              ) : archiveState === "loading" ? (
                <Button variant="outline" size="sm" disabled>
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  Creating...
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleGenerateArchive()}
                  disabled={task.status !== "completed"}
                  title={task.status !== "completed" ? "Task must be completed to create archive" : undefined}
                >
                  <Archive className="h-3.5 w-3.5 mr-1.5" />
                  Archive
                </Button>
              )
            )}

            {DELETABLE_STATUSES.has(task.status) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(true)}
                className="text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                Delete
              </Button>
            )}
          </div>
        }
      />

      <div
        className={cn(
          "overflow-hidden transition-[opacity,max-height,margin] duration-200 ease-out",
          showProgressCard ? "max-h-40 opacity-100" : "max-h-0 opacity-0 pointer-events-none",
          isProgressExiting && "opacity-0 max-h-0",
        )}
        style={{ transitionDuration: `${PROGRESS_EXIT_MS}ms` }}
      >
        <div className="glass-card p-5 space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">Screenshots</span>
            <StatusBadge status={task.status} />
          </div>
          <TaskProgressBar
            status={task.status}
            done={task.progress_done}
            total={task.progress_total}
            failed={task.progress_failed}
          />
          <p className="text-xs text-muted-foreground">Auto-refreshing every 3 seconds...</p>
        </div>
      </div>

      <Separator />

      <Tabs defaultValue="screenshots">
        <TabsList>
          <TabsTrigger value="screenshots">
            Screenshots
            {task.progress_total > 0 && (
              <span className="ml-1.5 text-xs text-muted-foreground">({task.progress_total})</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="logs">
            Logs
            {failedCount > 0 && (
              <span className="ml-1.5 text-xs text-destructive font-medium">{failedCount} errors</span>
            )}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="screenshots" className="mt-4">
          <ScreenshotsPage taskId={task.id} screenshots={screenshots} />
        </TabsContent>
        <TabsContent value="logs" className="mt-4">
          <LogsTable screenshots={screenshots} />
        </TabsContent>
      </Tabs>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete task"
        description={`This will permanently delete task #${shortId(task.id)}.`}
        confirmLabel="Delete"
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}
