import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const STATUS_CLASSES: Record<string, string> = {
  pending: "bg-status-pending-bg text-status-pending-fg",
  running: "bg-status-running-bg text-status-running-fg",
  completed: "bg-status-completed-bg text-status-completed-fg",
  failed: "bg-status-failed-bg text-status-failed-fg",
  cancelled: "bg-status-cancelled-bg text-status-cancelled-fg",
  timed_out: "bg-status-timed-out-bg text-status-timed-out-fg",
}

export function StatusBadge({ status }: { status: string }) {
  // Screenshot rows report "ok" where tasks report "completed".
  const key = status === "ok" ? "completed" : status

  return (
    <Badge className={cn("capitalize", STATUS_CLASSES[key] ?? STATUS_CLASSES.cancelled)}>
      {status === "running" && (
        <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-status-running-dot" />
      )}
      {status.replace(/_/g, " ")}
    </Badge>
  )
}
