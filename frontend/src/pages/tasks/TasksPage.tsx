import { useState, useMemo } from "react"
import { useParams, useNavigate, Link } from "react-router-dom"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useForm, useFieldArray } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Plus, ArrowLeft, Trash2, ChevronDown, Square } from "lucide-react"
import { toast } from "sonner"
import { tasksApi } from "@/api/tasks"
import { projectsApi } from "@/api/projects"
import { extractApiError } from "@/lib/utils"
import type { SitemapURL } from "@/types"
import { taskColumns } from "@/components/tasks/taskColumns"
import { normalizeViewport } from "@/lib/viewportUtils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { DataTable, type RowAction } from "@/components/ui/DataTable"
import type { Task } from "@/types"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"


const viewportSchema = z.object({
  width: z.number().int().min(320),
  height: z.number().int().min(240),
})

const taskSchema = z.object({
  selectedUrlIds: z.array(z.string()).min(1, "Select at least one URL"),
  override_browser: z.string().optional(),
  override_viewports: z.array(viewportSchema).optional(),
})

type TaskFormValues = z.infer<typeof taskSchema>

function groupByCpt(urls: SitemapURL[]): Record<string, SitemapURL[]> {
  return urls.reduce<Record<string, SitemapURL[]>>((acc, u) => {
    const key = u.cpt
    acc[key] = acc[key] ? [...acc[key], u] : [u]
    return acc
  }, {})
}

export default function TasksPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [overridesOpen, setOverridesOpen] = useState(false)
  const [useViewportOverride, setUseViewportOverride] = useState(false)

  const tasksQuery = useQuery({
    queryKey: ["tasks", projectId],
    queryFn: () => tasksApi.list({ projectId: projectId! }),
    enabled: !!projectId,
  })

  const sitemapQuery = useQuery({
    queryKey: ["sitemap-urls", projectId],
    queryFn: () => projectsApi.getSitemapUrls(projectId!),
    enabled: !!projectId && open,
  })

  const configQuery = useQuery({
    queryKey: ["project-config", projectId],
    queryFn: () => projectsApi.getConfig(projectId!),
    enabled: !!projectId && open,
  })

  const {
    register,
    handleSubmit,
    control,
    watch,
    setValue,
    reset,
    formState: { errors },
  } = useForm<TaskFormValues>({
    resolver: zodResolver(taskSchema),
    defaultValues: { selectedUrlIds: [] },
  })

  const { fields: vpFields, append: vpAppend, remove: vpRemove } = useFieldArray({
    control,
    name: "override_viewports",
  })

  const selectedUrlIds = watch("selectedUrlIds")

  const sitemapUrls = sitemapQuery.data ?? []
  const enabledUrls = sitemapUrls.filter((u) => u.is_enabled)
  const grouped = groupByCpt(enabledUrls)
  const config = configQuery.data

  const estimatedCount = useMemo(() => {
    const urlCount = selectedUrlIds.length
    if (!urlCount || !config) return 0
    const viewports = useViewportOverride && vpFields.length > 0 ? vpFields : config.viewports
    const browsersTotal = viewports.reduce((sum, vp) => {
      const browsers = (vp as { browsers?: string[] }).browsers || config.default_browsers
      return sum + browsers.length
    }, 0)
    return urlCount * browsersTotal
  }, [selectedUrlIds.length, useViewportOverride, vpFields, config])

  const cancelMutation = useMutation({
    mutationFn: (taskId: string) => tasksApi.cancel(taskId),
    onSuccess: () => {
      toast.success("Task stopped.")
      void queryClient.invalidateQueries({ queryKey: ["tasks", projectId] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => tasksApi.delete(taskId),
    onSuccess: () => {
      toast.success("Task deleted.")
      void queryClient.invalidateQueries({ queryKey: ["tasks", projectId] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  const createMutation = useMutation({
    mutationFn: (values: TaskFormValues) => {
      const targetUrls = sitemapUrls
        .filter((u) => values.selectedUrlIds.includes(u.id))
        .map((u) => u.url)

      return tasksApi.create({
        project_id: projectId!,
        target_urls: targetUrls,
        ...(values.override_browser ? { override_browser: values.override_browser } : {}),
        ...(useViewportOverride && values.override_viewports?.length
          ? { override_viewports: values.override_viewports.map(normalizeViewport) }
          : {}),
      })
    },
    onSuccess: (task) => {
      toast.success("Task created and enqueued.")
      setOpen(false)
      reset()
      void queryClient.invalidateQueries({ queryKey: ["tasks", projectId] })
      navigate(`/tasks/${task.id}`)
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  const toggleUrl = (id: string, checked: boolean) => {
    const current = selectedUrlIds
    setValue(
      "selectedUrlIds",
      checked ? [...current, id] : current.filter((x) => x !== id),
    )
  }

  const toggleAll = (ids: string[], checked: boolean) => {
    const current = selectedUrlIds
    if (checked) {
      setValue("selectedUrlIds", [...new Set([...current, ...ids])])
    } else {
      setValue("selectedUrlIds", current.filter((id) => !ids.includes(id)))
    }
  }

  const closeDialog = () => {
    setOpen(false)
    reset()
    setUseViewportOverride(false)
    setOverridesOpen(false)
  }

  const tasks = tasksQuery.data?.items ?? []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/projects/${projectId}`)}
            className="p-1 h-auto"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h1 className="text-2xl font-bold">Tasks</h1>
        </div>
        <Button onClick={() => navigate(`/tasks/new?project_id=${projectId}`)}>
          <Plus className="h-4 w-4 mr-2" />
          New Task
        </Button>
      </div>

      {/* Tasks table */}
      <DataTable
        columns={taskColumns}
        data={tasks}
        rowKey={(row) => row.id}
        onRowClick={(row) => navigate(`/tasks/${row.id}`)}
        isLoading={tasksQuery.isLoading}
        rowActions={(task) => {
          const actions: RowAction<Task>[] = [
            {
              label: "View",
              onClick: () => navigate(`/tasks/${task.id}`),
            },
          ]

          if (task.status === "pending" || task.status === "running") {
            actions.push({
              label: "Stop task",
              icon: <Square className="h-3.5 w-3.5" />,
              onClick: () => cancelMutation.mutate(task.id),
              variant: "destructive" as const,
            })
          }

          if (
            task.status === "completed" ||
            task.status === "failed" ||
            task.status === "cancelled" ||
            task.status === "timed_out"
          ) {
            actions.push({
              label: "Delete task",
              icon: <Trash2 className="h-3.5 w-3.5" />,
              onClick: () => deleteMutation.mutate(task.id),
              variant: "destructive" as const,
            })
          }

          return actions
        }}
        emptyState={{
          title: "No tasks yet",
          description: "Create one to start screenshotting.",
        }}
      />

      {/* Create Task Dialog */}
      <Dialog open={open} onOpenChange={(v: boolean) => (v ? setOpen(true) : closeDialog())}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Create task</DialogTitle>
            <DialogDescription>
              Select URLs to screenshot and optionally override project defaults.
            </DialogDescription>
          </DialogHeader>

          <form
            onSubmit={handleSubmit((v: TaskFormValues) => createMutation.mutate(v))}
            className="space-y-5 mt-2"
          >
            {/* URL selection */}
            <div>
              <Label className="mb-2 block">URLs to screenshot</Label>
              {sitemapQuery.isLoading ? (
                <div className="text-sm text-muted-foreground">Loading URLs…</div>
              ) : enabledUrls.length === 0 ? (
                <div className="text-sm text-muted-foreground">
                  No enabled URLs found.{" "}
                  <Link to={`/projects/${projectId}`} className="text-primary underline">
                    Add URLs in the Sitemap tab.
                  </Link>
                </div>
              ) : (
                <div className="border max-h-64 overflow-y-auto p-2 space-y-2">
                  {Object.entries(grouped).map(([cpt, cptUrls]) => {
                    const ids = cptUrls.map((u) => u.id)
                    const allChecked = ids.every((id) => selectedUrlIds.includes(id))
                    return (
                      <Collapsible key={cpt} defaultOpen>
                        <CollapsibleTrigger className="flex items-center gap-2 w-full text-left py-1">
                          <Checkbox
                            checked={allChecked}
                            onCheckedChange={(checked: boolean | "indeterminate") =>
                              toggleAll(ids, checked === true)
                            }
                            onClick={(e: React.MouseEvent) => e.stopPropagation()}
                          />
                          <span className="font-medium text-sm capitalize">{cpt}</span>
                          <span className="text-xs text-muted-foreground">
                            ({cptUrls.length})
                          </span>
                          <ChevronDown className="h-3 w-3 ml-auto text-muted-foreground" />
                        </CollapsibleTrigger>
                        <CollapsibleContent>
                          <div className="pl-6 space-y-1 py-1">
                            {cptUrls.map((u) => (
                              <div key={u.id} className="flex items-center gap-2 py-0.5">
                                <Checkbox
                                  checked={selectedUrlIds.includes(u.id)}
                                  onCheckedChange={(checked: boolean | "indeterminate") =>
                                    toggleUrl(u.id, checked === true)
                                  }
                                />
                                <span className="text-xs truncate max-w-sm">{u.url}</span>
                              </div>
                            ))}
                          </div>
                        </CollapsibleContent>
                      </Collapsible>
                    )
                  })}
                </div>
              )}
              {errors.selectedUrlIds && (
                <p className="text-sm text-destructive mt-1">{errors.selectedUrlIds.message}</p>
              )}
            </div>

            {/* Estimated count */}
            {selectedUrlIds.length > 0 && (
              <p className="text-sm text-muted-foreground">
                Estimated screenshots:{" "}
                <span className="font-semibold text-foreground">{estimatedCount}</span> (
                {selectedUrlIds.length} URL{selectedUrlIds.length !== 1 ? "s" : ""} ×{" "}
                {useViewportOverride && vpFields.length > 0
                  ? vpFields.length
                  : (config?.viewports.length ?? 1)}{" "}
                viewport
                {((useViewportOverride ? vpFields.length : config?.viewports.length) ?? 1) !== 1
                  ? "s"
                  : ""}
                )
              </p>
            )}

            <Separator />

            {/* Overrides section */}
            <Collapsible open={overridesOpen} onOpenChange={setOverridesOpen}>
              <CollapsibleTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="flex items-center gap-2"
                >
                  <ChevronDown
                    className={`h-4 w-4 transition-transform ${overridesOpen ? "rotate-180" : ""}`}
                  />
                  Override defaults (optional)
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-4 pt-3">
                {/* Browser override */}
                <div className="space-y-1">
                  <Label>Browser override</Label>
                  <Select onValueChange={(v: string) => setValue("override_browser", v)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Use project default" />
                    </SelectTrigger>
                    <SelectContent>
                      {["chrome", "firefox", "webkit"].map((b) => (
                        <SelectItem key={b} value={b}>
                          {b}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {/* Viewports override */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Checkbox
                      checked={useViewportOverride}
                      onCheckedChange={(v: boolean | "indeterminate") =>
                        setUseViewportOverride(v === true)
                      }
                    />
                    <Label>Override viewports</Label>
                  </div>
                  {useViewportOverride && (
                    <div className="space-y-2 pl-6">
                      {vpFields.map((field, index) => (
                        <div key={field.id} className="flex items-center gap-2">
                          <Input
                            type="number"
                            placeholder="Width"
                            {...register(`override_viewports.${index}.width`, {
                              valueAsNumber: true,
                            })}
                            className="w-24"
                          />
                          <span className="text-muted-foreground">×</span>
                          <Input
                            type="number"
                            placeholder="Height"
                            {...register(`override_viewports.${index}.height`, {
                              valueAsNumber: true,
                            })}
                            className="w-24"
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={() => vpRemove(index)}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      ))}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => vpAppend({ width: 1280, height: 720 })}
                      >
                        <Plus className="h-3 w-3 mr-1" />
                        Add viewport
                      </Button>
                    </div>
                  )}
                </div>
              </CollapsibleContent>
            </Collapsible>

            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" onClick={closeDialog}>
                Cancel
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? "Creating…" : "Create task"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
