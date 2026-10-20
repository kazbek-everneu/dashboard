import { useState, useMemo } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Plus, ListTodo, CheckCircle, XCircle, Loader2, Search, TimerOff } from "lucide-react"
import { tasksApi } from "@/api/tasks"
import { projectsApi } from "@/api/projects"
import type { Task, Project } from "@/types"
import PageHeader from "@/components/layout/PageHeader"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { StatsCard } from "@/components/ui/StatsCard"
import { FilterToolbar } from "@/components/ui/FilterToolbar"
import { DataTable, type ColumnDef } from "@/components/ui/DataTable"
import { taskColumns } from "@/components/tasks/taskColumns"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// ─── Types ────────────────────────────────────────────────────────────────────

interface EnrichedTask extends Task {
  project_name: string
  project_url: string
}

// ─── Column definitions ───────────────────────────────────────────────────────

const projectColumn: ColumnDef<EnrichedTask> = {
  key: "project",
  header: "Project",
  width: "w-[200px]",
  cell: (row) => (
    <div>
      <p className="font-medium text-foreground">{row.project_name}</p>
      <p className="text-xs text-muted-foreground truncate max-w-[180px]">{row.project_url}</p>
    </div>
  ),
}

const enrichedTaskColumns: ColumnDef<EnrichedTask>[] = [
  projectColumn,
  ...(taskColumns as ColumnDef<EnrichedTask>[]),
]

// ─── Page ─────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 20

export default function AllTasksPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [projectFilter, setProjectFilter] = useState(searchParams.get("project_id") ?? "")
  const [statusFilter, setStatusFilter] = useState("")
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({
    key: "created_at",
    direction: "desc",
  })
  const [page, setPage] = useState(1)

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list })
  const statsQuery = useQuery({ queryKey: ["tasks", "stats"], queryFn: tasksApi.stats })
  // Status and search filter the loaded page client-side, so they are not part
  // of the key (no refetch per keystroke); keep rows visible while paging.
  const tasksQuery = useQuery({
    queryKey: ["tasks", "all", projectFilter, page],
    queryFn: () =>
      tasksApi.list({
        limit: PAGE_SIZE,
        skip: (page - 1) * PAGE_SIZE,
        projectId: projectFilter || undefined,
      }),
    placeholderData: keepPreviousData,
  })

  const projectMap = useMemo(() => {
    const m: Record<string, Project> = {}
    for (const p of projectsQuery.data ?? []) m[p.id] = p
    return m
  }, [projectsQuery.data])

  const allTasks = tasksQuery.data?.items ?? []
  const running = statsQuery.data?.running ?? 0
  const completed = statsQuery.data?.completed ?? 0
  const failed = statsQuery.data?.failed ?? 0
  const timedOut = statsQuery.data?.timed_out ?? 0

  const clearFilters = () => {
    setProjectFilter("")
    setStatusFilter("")
    setSearch("")
    setPage(1)
  }
  const hasActiveFilters = projectFilter || statusFilter || search

  // ─── Derived data ──────────────────────────────────────────────────────────

  const rows = useMemo<EnrichedTask[]>(() => {
    let tasks = allTasks

    // Filter
    if (projectFilter) tasks = tasks.filter((t) => t.project_id === projectFilter)
    if (statusFilter) tasks = tasks.filter((t) => t.status === statusFilter)
    if (search) {
      const q = search.toLowerCase()
      tasks = tasks.filter((t) => {
        const proj = projectMap[t.project_id]
        return t.id.toLowerCase().includes(q) || (proj?.name.toLowerCase().includes(q) ?? false)
      })
    }

    // Enrich
    const enriched: EnrichedTask[] = tasks.map((t) => {
      const proj = projectMap[t.project_id]
      return {
        ...t,
        project_name: proj?.name ?? t.project_id.slice(0, 8),
        project_url: proj?.url ?? "",
      }
    })

    // Sort (running tasks always float to top via pinnedRows)
    return enriched.sort((a, b) => {
      let v = 0
      if (sort.key === "created_at")
        v = new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      else if (sort.key === "status") v = a.status.localeCompare(b.status)
      else if (sort.key === "progress") {
        const ap = a.progress_total > 0 ? a.progress_done / a.progress_total : 0
        const bp = b.progress_total > 0 ? b.progress_done / b.progress_total : 0
        v = ap - bp
      }
      return sort.direction === "asc" ? v : -v
    })
  }, [allTasks, projectFilter, statusFilter, search, projectMap, sort])

  const runningRows = rows.filter((r) => r.status === "running")
  const pageRows = rows

  return (
    <div className="page-shell">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>}
        description="View and manage screenshot tasks across all projects"
        meta={`${tasksQuery.data?.total ?? 0} indexed task${(tasksQuery.data?.total ?? 0) === 1 ? "" : "s"}`}
        actions={
          <Button onClick={() => navigate("/tasks/new")}>
            <Plus data-icon="inline-start" />
            New Task
          </Button>
        }
      />

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatsCard icon={ListTodo} value={statsQuery.data?.total ?? 0} label="Total Tasks" />
        <StatsCard icon={Loader2} value={running} label="Running" pulse={running > 0} />
        <StatsCard icon={CheckCircle} value={completed} label="Completed" />
        <StatsCard icon={XCircle} value={failed} label="Failed" />
        <StatsCard icon={TimerOff} value={timedOut} label="Timed Out" />
      </div>

      <FilterToolbar
        filters={
          <>
            <Select
              value={projectFilter || "all"}
              onValueChange={(v) => {
                setProjectFilter(v === "all" ? "" : v)
                setPage(1)
              }}
            >
              <SelectTrigger className="w-full sm:w-48" aria-label="Filter by project">
                <SelectValue placeholder="All projects" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                {(projectsQuery.data ?? []).map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={statusFilter || "all"}
              onValueChange={(v) => {
                setStatusFilter(v === "all" ? "" : v)
                setPage(1)
              }}
            >
              <SelectTrigger className="w-full sm:w-40" aria-label="Filter by status">
                <SelectValue placeholder="All statuses" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                <SelectItem value="pending">Pending</SelectItem>
                <SelectItem value="running">Running</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
                <SelectItem value="cancelled">Cancelled</SelectItem>
                <SelectItem value="timed_out">Timed out</SelectItem>
              </SelectContent>
            </Select>

            <InputGroup className="w-full sm:w-72">
              <InputGroupAddon>
                <Search />
              </InputGroupAddon>
              <InputGroupInput
                placeholder="Search by project or task ID…"
                aria-label="Search tasks"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setPage(1)
                }}
              />
            </InputGroup>
          </>
        }
        actions={
          hasActiveFilters ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearFilters}
              className="text-muted-foreground"
            >
              Clear filters
            </Button>
          ) : undefined
        }
      />

      {/* Table */}
      <DataTable
        columns={enrichedTaskColumns}
        data={pageRows}
        rowKey={(row) => row.id}
        onRowClick={(row) => navigate(`/tasks/${row.id}`)}
        pinnedRows={runningRows}
        isLoading={tasksQuery.isLoading}
        sort={sort}
        onSortChange={(key, direction) => {
          setSort({ key, direction })
          setPage(1)
        }}
        pagination={
          (tasksQuery.data?.total ?? 0) > PAGE_SIZE
            ? {
                page,
                pageSize: PAGE_SIZE,
                total: tasksQuery.data?.total ?? 0,
                onPageChange: setPage,
              }
            : undefined
        }
        emptyState={
          (tasksQuery.data?.total ?? 0) === 0
            ? {
                icon: <ListTodo />,
                title: "No tasks yet",
                description: "Create your first task to start screenshotting.",
                action: (
                  <Button onClick={() => navigate("/tasks/new")}>
                    <Plus data-icon="inline-start" />
                    New Task
                  </Button>
                ),
              }
            : {
                icon: <ListTodo />,
                title: "No tasks match your filters",
                action: (
                  <Button variant="outline" size="sm" onClick={clearFilters}>
                    Clear filters
                  </Button>
                ),
              }
        }
      />
    </div>
  )
}
