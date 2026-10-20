import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import { useNavigate, useSearchParams, Link } from "react-router-dom"
import { useQuery, useMutation } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { ArrowRight, Camera, FolderOpen, Globe } from "lucide-react"
import { tasksApi } from "@/api/tasks"
import { projectsApi } from "@/api/projects"
import { extractApiError } from "@/lib/helpers"
import { DEVICE_EMULATION_HINT, normalizeViewport } from "@/lib/viewportUtils"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
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

  const contextProjectLabel = contextProjectName ?? queryProjectId
  const urlCount = selectedUrlIds.length
  const viewportCount = taskConfig.viewports.length

  return (
    <div className="flex max-w-6xl flex-col gap-6">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Create New Task</h1>}
        description="Configure and launch a new screenshot task"
      />

      <form onSubmit={handleSubmit((v) => createMutation.mutate(v))}>
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          {/* ── LEFT COLUMN — Configuration ── */}
          <div className="flex shrink-0 flex-col gap-4 lg:w-[460px]">
            <Card>
              <CardHeader className="border-b">
                <CardTitle>Configuration</CardTitle>
                <CardDescription>Browsers and viewports default to the project settings.</CardDescription>
              </CardHeader>
              <CardContent>
                <FieldGroup>
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
                    <>
                      <FieldSeparator />
                      <FieldSet>
                        <FieldLegend variant="label">Browsers</FieldLegend>
                        <FieldDescription>Browsers to capture with in this task.</FieldDescription>
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
                      </FieldSet>

                      <FieldSeparator />
                      <FieldSet>
                        <FieldLegend variant="label">Viewports</FieldLegend>
                        <FieldDescription>
                          Screen sizes to capture in this task. {DEVICE_EMULATION_HINT}
                        </FieldDescription>
                        <ViewportList
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

            {/* Estimated counter + submit */}
            <Card>
              <CardHeader>
                <CardDescription>Estimated screenshots</CardDescription>
                <CardTitle className="text-3xl font-semibold tabular-nums">{estimatedCount}</CardTitle>
                {urlCount > 0 && viewportCount > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {urlCount} URL{urlCount !== 1 ? "s" : ""} × {viewportCount} viewport
                    {viewportCount !== 1 ? "s" : ""}
                  </p>
                )}
              </CardHeader>
              <CardFooter className="flex-col items-stretch gap-2">
                {errors.selectedUrlIds && <FieldError>{errors.selectedUrlIds.message}</FieldError>}
                <Button type="submit" className="w-full" disabled={createMutation.isPending}>
                  {createMutation.isPending ? <Spinner data-icon="inline-start" /> : <Camera data-icon="inline-start" />}
                  {createMutation.isPending ? "Creating…" : "Create task"}
                </Button>
              </CardFooter>
            </Card>
          </div>

          {/* ── RIGHT COLUMN — URL Selection ── */}
          <div className="min-w-0 flex-1">
            {!selectedProjectId ? (
              <StateMessage
                icon={<FolderOpen />}
                title="Select a project"
                description="Its sitemap pages will appear here."
                className="min-h-[500px]"
              />
            ) : enabledUrls.length === 0 && !sitemapQuery.isLoading ? (
              <StateMessage
                icon={<Globe />}
                title="No sitemap pages yet"
                description="Fetch the sitemap on the project page first."
                className="min-h-[500px]"
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
        </div>
      </form>
    </div>
  )
}
