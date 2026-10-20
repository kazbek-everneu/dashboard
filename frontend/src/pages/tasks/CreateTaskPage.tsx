import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import { useNavigate, useSearchParams, Link } from "react-router-dom"
import { useQuery, useMutation } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { tasksApi } from "@/api/tasks"
import { projectsApi } from "@/api/projects"
import { extractApiError } from "@/lib/helpers"
import { normalizeViewport } from "@/lib/viewportUtils"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { Button } from "@/components/ui/button"
import { FormField } from "@/components/ui/FormField"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { SectionHeading } from "@/components/ui/SectionHeading"
import { UrlSelector } from "@/components/sitemap/UrlSelector"
import { BrowserMultiSelect } from "@/components/settings/BrowserMultiSelect"
import { ViewportList } from "@/components/settings/ViewportList"
import type { ViewportConfig } from "@/types"

interface TaskOverrideConfig {
  default_browsers: string[]
  viewports: ViewportConfig[]
}

const taskSchema = z.object({
  project_id: z.string().min(1, "Select a project"),
  selectedUrlIds: z.array(z.string()).min(1, "Select at least one URL"),
})

type TaskFormValues = z.infer<typeof taskSchema>

export default function CreateTaskPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const queryProjectId = searchParams.get("project_id") ?? ""


  const [taskConfig, setTaskConfig] = useState<TaskOverrideConfig>({
    default_browsers: ["chrome"],
    viewports: [],
  })

  // Track which project id the taskConfig was last initialised for
  const configInitProjectRef = useRef<string>("")

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

  const config = configQuery.data
  const sitemapUrls = sitemapQuery.data ?? []
  const enabledUrls = sitemapUrls.filter((u) => u.is_enabled)

  // Initialise taskConfig from project config whenever the selected project changes
  useEffect(() => {
    if (config && selectedProjectId && selectedProjectId !== configInitProjectRef.current) {
      configInitProjectRef.current = selectedProjectId
      setTaskConfig({
        default_browsers: config.default_browsers,
        viewports: config.viewports.map(normalizeViewport),
      })
    }
  }, [config, selectedProjectId])

  const estimatedCount = useMemo(() => {
    const urlCount = selectedUrlIds.length
    if (!urlCount || !taskConfig.viewports.length) return 0
    const browsersTotal = taskConfig.viewports.reduce((sum, vp) => {
      return sum + (vp.browsers ?? taskConfig.default_browsers).length
    }, 0)
    return urlCount * browsersTotal
  }, [selectedUrlIds.length, taskConfig])

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

  return (
    <div className="space-y-6 max-w-6xl">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Create New Task</h1>}
        description="Configure and launch a new screenshot task"
      />

      <form onSubmit={handleSubmit((v) => createMutation.mutate(v))}>
        <div className="flex flex-col lg:flex-row gap-6">
          {/* ── LEFT COLUMN — Configuration ── */}
          <div className="lg:w-[460px] shrink-0 space-y-5">

            {/* Project selector */}
            <FormField
              label="Project"
              required
              error={queryProjectId ? undefined : errors.project_id?.message}
              className="space-y-1.5"
            >
              {queryProjectId ? (
                <div className="border bg-muted/40 px-3 py-2 text-sm">
                  {projectsQuery.data?.find((p) => p.id === queryProjectId)?.name ?? queryProjectId}
                </div>
              ) : (
                <>
                  <Select
                    value={selectedProjectId || undefined}
                    onValueChange={(v) => {
                      setValue("project_id", v)
                      setValue("selectedUrlIds", [])
                    }}
                  >
                    <SelectTrigger className="w-full">
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
                </>
              )}
            </FormField>

            {config && (
              <>
                <Separator />

                {/* ── Browsers ── */}
                <div className="space-y-2">
                  <SectionHeading title="Browsers" />
                  <p className="text-xs text-muted-foreground">
                    Select browsers for this task. Defaults loaded from project settings.
                  </p>
                  <BrowserMultiSelect
                    selected={taskConfig.default_browsers}
                    defaultBrowsers={config.default_browsers}
                    onChange={(browsers) =>
                      setTaskConfig((c) => ({
                        ...c,
                        default_browsers: browsers ?? c.default_browsers,
                      }))
                    }
                  />
                </div>

                <Separator />

                {/* ── Viewports ── */}
                <div className="space-y-2">
                  <SectionHeading title="Viewports" />
                  <p className="text-xs text-muted-foreground">
                    Configure screen sizes for this task. Defaults loaded from project settings.
                  </p>
                  <ViewportList
                    viewports={taskConfig.viewports}
                    onChange={(vps) => setTaskConfig((c) => ({ ...c, viewports: vps }))}
                    defaultBrowsers={taskConfig.default_browsers}
                  />
                </div>
              </>
            )}

            <Separator />

            {/* Estimated counter + submit */}
            <div className="border bg-muted/30 p-4 space-y-3">
              <p className="text-sm font-medium">Estimated screenshots</p>
              <p className="text-3xl font-bold text-center">{estimatedCount}</p>
              {selectedUrlIds.length > 0 && taskConfig.viewports.length > 0 && (
                <p className="text-xs text-muted-foreground text-center">
                  {selectedUrlIds.length} URL{selectedUrlIds.length !== 1 ? "s" : ""} ×{" "}
                  {taskConfig.viewports.length} viewport
                  {taskConfig.viewports.length !== 1 ? "s" : ""}
                </p>
              )}
              {errors.selectedUrlIds && (
                <p className="text-sm text-destructive text-center">
                  {errors.selectedUrlIds.message}
                </p>
              )}
              <Button
                type="submit"
                className="w-full"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Creating…" : "Create Task"}
              </Button>
            </div>
          </div>

          {/* ── RIGHT COLUMN — URL Selection ── */}
          <div className="flex-1 min-w-0">
            {!selectedProjectId ? (
              <div className="border flex min-h-[500px] items-center justify-center px-6 text-center text-sm text-muted-foreground">
                Select a project to load pages
              </div>
            ) : enabledUrls.length === 0 && !sitemapQuery.isLoading ? (
              <div className="border flex min-h-[500px] flex-col items-center justify-center gap-2 px-6 text-center">
                <p className="text-sm text-muted-foreground">
                  No sitemap found. Fetch sitemap in the Sitemap tab first.
                </p>
                <Link
                  to={`/projects/${selectedProjectId}`}
                  className="text-xs text-primary underline"
                >
                  Go to project sitemap →
                </Link>
              </div>
            ) : (
              <UrlSelector
                urls={enabledUrls}
                selectedIds={new Set(selectedUrlIds)}
                onSelectionChange={handleSelectionChange}
                isLoading={sitemapQuery.isLoading}
              />
            )}
            {errors.selectedUrlIds && (
              <p className="text-sm text-destructive mt-2">{errors.selectedUrlIds.message}</p>
            )}
          </div>
        </div>
      </form>
    </div>
  )
}
