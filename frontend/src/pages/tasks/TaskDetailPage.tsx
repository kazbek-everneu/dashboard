import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Square, Trash2, Download, Archive } from "lucide-react"
import { toast } from "sonner"
import { useTaskPolling } from "@/hooks/useTaskPolling"
import { tasksApi } from "@/api/tasks"
import { screenshotsApi } from "@/api/screenshots"
import { projectsApi } from "@/api/projects"
import { cn } from "@/lib/utils"
import { shortId, extractApiError } from "@/lib/helpers"
import { apiDateMs } from "@/lib/datetime"
import { DateTime } from "@/components/ui/DateTime"
import { formatFileSize } from "@/lib/formatters"
import { formatElapsedTime, formatThroughput, formatAverageSeconds, browserLabel } from "@/lib/task-formatters"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { TaskProgressBar } from "@/components/tasks/TaskProgressBar"
import { TaskStatsBar } from "@/components/tasks/TaskStatsBar"
import { LogsTable } from "@/components/tasks/LogsTable"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { StateMessage } from "@/components/ui/StateMessage"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
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
  const [wasRunning, setWasRunning] = useState(false)

  const { data: task, isLoading } = useTaskPolling(id!)
  const isRunning = task?.status === "running"

  // Show the live progress card once the task runs (state adjusted during
  // render); when it stops, the card animates out before it is removed.
  if (isRunning !== wasRunning) {
    setWasRunning(isRunning)
    if (isRunning) setShowProgressCard(true)
  }
  const isProgressExiting = showProgressCard && !isRunning

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
    if (!isProgressExiting) return
    const timeoutId = window.setTimeout(() => setShowProgressCard(false), PROGRESS_EXIT_MS)
    return () => window.clearTimeout(timeoutId)
  }, [isProgressExiting])

  // The screenshot list only polls while the task runs, so captures written at
  // the very end were missed; fetch the final list once the task finishes.
  const taskStatus = task?.status
  const prevStatusRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    const prevStatus = prevStatusRef.current
    prevStatusRef.current = taskStatus
    if (prevStatus && !FINAL_STATUSES.has(prevStatus) && taskStatus && FINAL_STATUSES.has(taskStatus)) {
      void queryClient.invalidateQueries({ queryKey: ["screenshots", id] })
    }
  }, [taskStatus, id, queryClient])

  // "Now" for tasks without a completion time, fixed when the page opens
  // (the metrics strip that uses it only shows for finished tasks).
  const [openedAt] = useState(() => Date.now())

  const startedAtIso = task?.started_at
  const completedAtIso = task?.completed_at
  const elapsedMs = useMemo(() => {
    if (!startedAtIso) return null
    const startedAt = apiDateMs(startedAtIso)
    const endAt = completedAtIso ? apiDateMs(completedAtIso) : openedAt
    const diff = endAt - startedAt
    return diff > 0 ? diff : null
  }, [startedAtIso, completedAtIso, openedAt])

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

  // Only successful captures: a failed row's duration is the time spent across
  // its attempts, not a goto → screenshot capture time.
  const averageCaptureSeconds = useMemo(() => {
    const captured = screenshots.filter((s) => s.status === "ok")
    return captured.length > 0
      ? captured.reduce((sum, s) => sum + s.duration_seconds, 0) / captured.length
      : null
  }, [screenshots])

  useBreadcrumbs([
    { label: "Tasks", to: "/tasks" },
    ...(project ? [{ label: project.name, to: `/projects/${project.id}` }] : []),
    { label: task ? `Task #${shortId(task.id)}` : "Task" },
  ])

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-72" />
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-80 w-full" />
      </div>
    )
  }
  if (!task) {
    return (
      <StateMessage
        compact
        tone="destructive"
        title="Task not found"
        description="It may have been deleted."
        action={
          <Button asChild variant="outline">
            <Link to="/tasks">Back to tasks</Link>
          </Button>
        }
      />
    )
  }

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
    { label: "Done", value: okCount.toLocaleString(), valueClassName: "text-sm font-semibold text-success" },
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
    <div className="flex flex-col gap-6">
      <PageHeader
        title={
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">Task #{shortId(task.id)}</h1>
            <StatusBadge status={task.status} />
          </div>
        }
        description={
          <>
            {project ? `${project.name} · ` : ""}
            <DateTime value={task.created_at} />
          </>
        }
        footerClassName="pt-1"
        footer={showStatsRow ? (
          <TaskStatsBar items={statsItems} />
        ) : null}
        actions={
          <>
            {ACTIVE_STATUSES.has(task.status) && (
              <Button
                variant="destructive"
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
              >
                {cancelMutation.isPending ? <Spinner data-icon="inline-start" /> : <Square data-icon="inline-start" />}
                {cancelMutation.isPending ? "Stopping…" : "Stop"}
              </Button>
            )}

            {DELETABLE_STATUSES.has(task.status) && (
              archiveState === "ready" && archiveUrl ? (
                <Button variant="outline" asChild>
                  <a href={`${API_BASE}${archiveUrl}`} download={`task-${id}.zip`}>
                    <Download data-icon="inline-start" />
                    Download ZIP
                    <span className="text-xs text-muted-foreground">{formatFileSize(archiveSize)}</span>
                  </a>
                </Button>
              ) : archiveState === "loading" ? (
                <Button variant="outline" disabled>
                  <Spinner data-icon="inline-start" />
                  Creating archive…
                </Button>
              ) : task.status !== "completed" ? (
                // A disabled button gets no pointer events; the span carries the tooltip.
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span tabIndex={0} className="inline-flex">
                      <Button variant="outline" disabled>
                        <Archive data-icon="inline-start" />
                        Archive
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>Only completed tasks can be archived</TooltipContent>
                </Tooltip>
              ) : (
                <Button variant="outline" onClick={() => void handleGenerateArchive()}>
                  <Archive data-icon="inline-start" />
                  Archive
                </Button>
              )
            )}

            {DELETABLE_STATUSES.has(task.status) && (
              <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
                <Trash2 data-icon="inline-start" />
                Delete
              </Button>
            )}
          </>
        }
      />

      <div
        className={cn(
          "overflow-hidden transition-[opacity,max-height,margin] duration-200 ease-out",
          // -mt-6 cancels the parent gap while the card is collapsed.
          showProgressCard ? "max-h-40 opacity-100" : "pointer-events-none -mt-6 max-h-0 opacity-0",
          isProgressExiting && "-mt-6 max-h-0 opacity-0",
        )}
        style={{ transitionDuration: `${PROGRESS_EXIT_MS}ms` }}
      >
        <Card>
          <CardHeader>
            <CardTitle>Capturing screenshots</CardTitle>
            <CardDescription>Auto-refreshing every 3 seconds…</CardDescription>
            <CardAction>
              <StatusBadge status={task.status} />
            </CardAction>
          </CardHeader>
          <CardContent>
            <TaskProgressBar
              status={task.status}
              done={task.progress_done}
              total={task.progress_total}
              failed={task.progress_failed}
            />
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="screenshots" className="gap-4">
        <TabsList>
          <TabsTrigger value="screenshots">
            Screenshots
            {task.progress_total > 0 && (
              <Badge variant="secondary" className="tabular-nums">
                {task.progress_total}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="logs">
            Logs
            {failedCount > 0 && (
              <Badge variant="destructive" className="tabular-nums">
                {failedCount} {failedCount === 1 ? "error" : "errors"}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="screenshots">
          <ScreenshotsPage taskId={task.id} screenshots={screenshots} />
        </TabsContent>
        <TabsContent value="logs">
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
