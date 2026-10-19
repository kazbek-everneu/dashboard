import { TaskProgressBar } from "@/components/tasks/TaskProgressBar"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { formatRelativeTime, formatDate } from "@/lib/formatters"
import type { Task } from "@/types"
import type { ColumnDef } from "@/components/ui/DataTable"

export const taskColumns: ColumnDef<Task>[] = [
  {
    key: "status",
    header: "Status",
    width: "w-[130px]",
    sortKey: "status",
    cell: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: "progress",
    header: "Progress",
    width: "w-[180px]",
    cell: (row) => (
      <TaskProgressBar
        status={row.status}
        done={row.progress_done}
        total={row.progress_total}
        failed={row.progress_failed}
        compact
      />
    ),
  },
  {
    key: "pages",
    header: "Pages",
    width: "w-[80px]",
    hideOnMobile: true,
    cell: (row) => (
      <span className="text-sm text-muted-foreground">{row.target_urls.length}</span>
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
