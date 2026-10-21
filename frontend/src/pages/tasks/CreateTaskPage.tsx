import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import { useNavigate, useSearchParams, Link } from "react-router-dom"
import { useQuery, useMutation } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { ArrowRight, Camera, FolderOpen, Globe, RotateCcw } from "lucide-react"
import { tasksApi } from "@/api/tasks"
import { projectsApi } from "@/api/projects"
import { apiDateMs } from "@/lib/datetime"
import { extractApiError } from "@/lib/helpers"
import { formatElapsedTime } from "@/lib/task-formatters"
import { DEVICE_EMULATION_HINT, normalizeViewport } from "@/lib/viewportUtils"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLegend,
  FieldSeparator,
  FieldSet,
} from "@/components/ui/field"
import { FormField } from "@/components/ui/FormField"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { StateMessage } from "@/components/ui/StateMessage"
import { WarningAlert } from "@/components/ui/WarningAlert"
import { UrlSelector } from "@/components/sitemap/UrlSelector"
import { BrowserMultiSelect } from "@/components/settings/BrowserMultiSelect"
import { ViewportList } from "@/components/settings/ViewportList"
import type { ProjectConfig, Task, ViewportConfig } from "@/types"

interface TaskOverrideConfig {
  default_browsers: string[]
  viewports: ViewportConfig[]
}

const taskSchema = z.object({
  project_id: z.string().min(1, "Select a project"),
  selectedUrlIds: z.array(z.string()).min(1, "Select at least one URL"),
})

type TaskFormValues = z.infer<typeof taskSchema>

/** Above this many screenshots the page warns that the task will take a while. */
const LARGE_TASK_THRESHOLD = 500

function configFromProject(config: ProjectConfig): TaskOverrideConfig {
  return {
    default_browsers: config.default_browsers,
    viewports: config.viewports.map(normalizeViewport),
  }
}

const browsersKey = (browsers: string[] | null) => (browsers ? [...browsers].sort().join(",") : null)

function sameConfig(a: TaskOverrideConfig, b: TaskOverrideConfig): boolean {
  return (
    browsersKey(a.default_browsers) === browsersKey(b.default_browsers) &&
    a.viewports.length === b.viewports.length &&
    a.viewports.every((vp, i) => {
      const other = b.viewports[i]
      return (
        !!other &&
        vp.width === other.width &&
        vp.height === other.height &&
        vp.device_type === other.device_type &&
        browsersKey(vp.browsers) === browsersKey(other.browsers)
      )
    })
  )
}

const isActive = (task: Task) => task.status === "pending" || task.status === "running"

/** Capture time per screenshot in the project's latest completed task (tasks come newest first). */
function msPerScreenshot(tasks: Task[]): number | null {
  for (const t of tasks) {
    if (t.status === "completed" && t.started_at && t.completed_at && t.progress_total > 0) {
      return (apiDateMs(t.completed_at) - apiDateMs(t.started_at)) / t.progress_total
    }
  }
  return null
}

const plural = (count: number, word: string) => `${count} ${word}${count !== 1 ? "s" : ""}`

export default function CreateTaskPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const queryProjectId = searchParams.get("project_id") ?? ""


  const [taskConfig, setTaskConfig] = useState<TaskOverrideConfig>({
    default_browsers: ["chrome"],
    viewports: [],
  })
  // Which project taskConfig was last initialised for, and how often it was reset:
  // both remount the viewport list so its rows drop their local state.
  const [configProjectId, setConfigProjectId] = useState("")
  const [resetCount, setResetCount] = useState(0)

  const {
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<TaskFormValues>({
    resolver: zodResolver(taskSchema),
    defaultValues: {
      project_id: queryProjectId,
      selectedUrlIds: [],
    },
  })

  const selectedProjectId = watch("project_id")
  const selectedUrlIds = watch("selectedUrlIds")

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
  })

  // Opened from a project page: keep that project in the trail.
  const contextProjectName = projectsQuery.data?.find((p) => p.id === queryProjectId)?.name
  useBreadcrumbs(
    queryProjectId
      ? [
          { label: "Projects", to: "/projects" },
          { label: contextProjectName ?? "Project", to: `/projects/${queryProjectId}` },
          { label: "New task" },
        ]
      : [{ label: "Tasks", to: "/tasks" }, { label: "New task" }],
  )

  const configQuery = useQuery({
    queryKey: ["project-config", selectedProjectId],
    queryFn: () => projectsApi.getConfig(selectedProjectId),
    enabled: !!selectedProjectId,
  })

  const sitemapQuery = useQuery({
    queryKey: ["sitemap-urls", selectedProjectId],
    queryFn: () => projectsApi.getSitemapUrls(selectedProjectId),
    enabled: !!selectedProjectId,
  })

  // Same query as the project page: the latest tasks, newest first.
  const tasksQuery = useQuery({
    queryKey: ["tasks", selectedProjectId, { limit: 10 }],
    queryFn: () => tasksApi.list({ projectId: selectedProjectId, limit: 10 }),
    enabled: !!selectedProjectId,
    // Poll while a task runs, so the warning goes away when it ends.
    refetchInterval: (query) => (query.state.data?.items.some(isActive) ? 5000 : false),
  })

  const config = configQuery.data
  const sitemapUrls = sitemapQuery.data ?? []
  const enabledUrls = sitemapUrls.filter((u) => u.is_enabled)
  const projectTasks = tasksQuery.data?.items ?? []
  const activeTask = projectTasks.find(isActive)

  const projectDefaults = useMemo(() => (config ? configFromProject(config) : null), [config])

  // Initialise taskConfig from project config whenever the selected project changes
  // (during render, so the previous project's config is never shown).
  if (projectDefaults && selectedProjectId && selectedProjectId !== configProjectId) {
    setConfigProjectId(selectedProjectId)
    setTaskConfig(projectDefaults)
  }

  const isCustomised =
    !!projectDefaults &&
    configProjectId === selectedProjectId &&
    !sameConfig(taskConfig, projectDefaults)

  const resetConfig = () => {
    if (!projectDefaults) return
    setTaskConfig(projectDefaults)
    setResetCount((n) => n + 1)
  }

  const urlCount = selectedUrlIds.length
  const viewportCount = taskConfig.viewports.length
  // A viewport captures with its own browsers, else with the task's.
  const viewportBrowserCounts = taskConfig.viewports.map(
    (vp) => (vp.browsers ?? taskConfig.default_browsers).length,
  )
  const comboCount = viewportBrowserCounts.reduce((sum, n) => sum + n, 0)
  const estimatedCount = urlCount * comboCount
  const sameBrowserCount = viewportBrowserCounts.every((n) => n === viewportBrowserCounts[0])
  const estimateBreakdown =
    urlCount > 0 && viewportCount > 0
      ? sameBrowserCount
        ? `${plural(urlCount, "page")} × ${plural(viewportCount, "viewport")} × ${plural(viewportBrowserCounts[0]!, "browser")}`
        : `${plural(urlCount, "page")} × ${comboCount} viewport/browser combinations`
      : null

  const perScreenshotMs = msPerScreenshot(projectTasks)
  const estimatedDuration =
    perScreenshotMs !== null ? formatElapsedTime(perScreenshotMs * estimatedCount) : null

  const createMutation = useMutation({
    mutationFn: (values: TaskFormValues) => {
      const targetUrls = sitemapUrls
        .filter((u) => values.selectedUrlIds.includes(u.id))
        .map((u) => u.url)

      return tasksApi.create({
        project_id: values.project_id,
        target_urls: targetUrls,
        override_browsers: taskConfig.default_browsers,
        override_viewports: taskConfig.viewports,
      })
    },
    onSuccess: (task) => {
      toast.success("Task created and enqueued.")
      navigate(`/tasks/${task.id}`)
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  const handleSelectionChange = useCallback(
    (ids: Set<string>) => setValue("selectedUrlIds", [...ids]),
    [setValue],
  )

  // Pre-select all enabled URLs when sitemap data first loads for the current project.
  // The ref tracks which project has already been initialised so that a subsequent
  // urls-array reference change (e.g. React Query refetch) does not overwrite the
  // user's manual selection.
  const initialisedProjectRef = useRef<string | null>(null)
  useEffect(() => {
    if (enabledUrls.length > 0 && initialisedProjectRef.current !== selectedProjectId) {
      initialisedProjectRef.current = selectedProjectId
      setValue("selectedUrlIds", enabledUrls.map((u) => u.id))
    }
  }, [enabledUrls, selectedProjectId, setValue])

  const contextProjectLabel = contextProjectName ?? queryProjectId

  return (
    <form className="flex flex-col gap-6" onSubmit={handleSubmit((v) => createMutation.mutate(v))}>
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Create New Task</h1>}
        description="Configure and launch a new screenshot task"
        actions={
          <div className="flex items-center gap-4">
            <div className="flex flex-col gap-1 md:items-end">
              <p className="text-sm text-muted-foreground">
                <span className="text-2xl font-semibold tabular-nums text-foreground">
                  {estimatedCount}
                </span>{" "}
                screenshot{estimatedCount !== 1 ? "s" : ""}
              </p>
              {estimateBreakdown && (
                <p className="text-xs text-muted-foreground tabular-nums">{estimateBreakdown}</p>
              )}
              {errors.selectedUrlIds && <FieldError>{errors.selectedUrlIds.message}</FieldError>}
            </div>
            <Button type="submit" disabled={createMutation.isPending || !!activeTask}>
              {createMutation.isPending ? <Spinner data-icon="inline-start" /> : <Camera data-icon="inline-start" />}
              {createMutation.isPending ? "Creating…" : "Create task"}
            </Button>
          </div>
        }
      />

      {activeTask && (
        <WarningAlert
          title={
            activeTask.status === "running"
              ? "A task for this project is already running"
              : "A task for this project is already queued"
          }
        >
          A new task can start once it finishes or is cancelled.{" "}
          <Link to={`/tasks/${activeTask.id}`} className="font-medium underline underline-offset-4">
            View task
          </Link>
        </WarningAlert>
      )}

      {estimatedCount > LARGE_TASK_THRESHOLD && (
        <WarningAlert title={`Large task: ${estimatedCount} screenshots`}>
          {estimatedDuration &&
            `At the pace of this project's last task it takes about ${estimatedDuration}. `}
          Fewer pages, viewports or browsers make it shorter.
        </WarningAlert>
      )}

      {/* Configuration 60% / pages 40%; stacked below xl, where 60% is too narrow for a viewport row */}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] xl:items-start">
        <Card>
          <CardHeader className="border-b">
            <CardTitle>Configuration</CardTitle>
            <CardDescription>Browsers and viewports default to the project settings.</CardDescription>
            {isCustomised && (
              <CardAction>
                <Button type="button" variant="ghost" size="sm" onClick={resetConfig}>
                  <RotateCcw data-icon="inline-start" />
                  Reset to project defaults
                </Button>
              </CardAction>
            )}
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <div className="grid gap-6 md:grid-cols-2">
                <FormField
                  label="Project"
                  htmlFor="task-project"
                  required
                  error={queryProjectId ? undefined : errors.project_id?.message}
                >
                  {queryProjectId ? (
                    <Input id="task-project" readOnly value={contextProjectLabel} />
                  ) : (
                    <Select
                      value={selectedProjectId || undefined}
                      onValueChange={(v) => {
                        setValue("project_id", v)
                        setValue("selectedUrlIds", [])
                      }}
                    >
                      <SelectTrigger id="task-project" className="w-full">
                        <SelectValue placeholder="Select a project…" />
                      </SelectTrigger>
                      <SelectContent>
                        {(projectsQuery.data ?? []).map((p) => (
                          <SelectItem key={p.id} value={p.id}>
                            {p.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </FormField>

                {config && (
                  <FieldSet className="gap-2">
                    <FieldLegend variant="label">Browsers</FieldLegend>
                    <BrowserMultiSelect
                      selected={taskConfig.default_browsers}
                      defaultBrowsers={config.default_browsers}
                      // null = the selection is back to the project's browsers.
                      onChange={(browsers) =>
                        setTaskConfig((c) => ({
                          ...c,
                          default_browsers: browsers ?? config.default_browsers,
                        }))
                      }
                    />
                    <FieldDescription>
                      Used by every viewport that doesn't pick its own browsers.
                    </FieldDescription>
                  </FieldSet>
                )}
              </div>

              {config && (
                <>
                  <FieldSeparator />
                  <FieldSet>
                    <FieldLegend variant="label">Viewports</FieldLegend>
                    <FieldDescription>
                      Screen sizes to capture in this task. {DEVICE_EMULATION_HINT}
                    </FieldDescription>
                    <ViewportList
                      key={`${configProjectId}:${resetCount}`}
                      viewports={taskConfig.viewports}
                      onChange={(vps) => setTaskConfig((c) => ({ ...c, viewports: vps }))}
                      defaultBrowsers={taskConfig.default_browsers}
                    />
                  </FieldSet>
                </>
              )}
            </FieldGroup>
          </CardContent>
        </Card>

        {!selectedProjectId ? (
          <StateMessage
            icon={<FolderOpen />}
            title="Select a project"
            description="Its sitemap pages will appear here."
          />
        ) : enabledUrls.length === 0 && !sitemapQuery.isLoading ? (
          <StateMessage
            icon={<Globe />}
            title="No sitemap pages yet"
            description="Fetch the sitemap on the project page first."
            action={
              <Button asChild variant="outline">
                <Link to={`/projects/${selectedProjectId}`}>
                  Go to project sitemap
                  <ArrowRight data-icon="inline-end" />
                </Link>
              </Button>
            }
          />
        ) : (
          <UrlSelector
            urls={enabledUrls}
            selectedIds={new Set(selectedUrlIds)}
            onSelectionChange={handleSelectionChange}
            isLoading={sitemapQuery.isLoading}
          />
        )}
      </div>
    </form>
  )
}
